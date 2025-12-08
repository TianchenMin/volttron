# -*- coding: utf-8 -*-
# ===----------------------------------------------------------------------===
#
#                 Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2023 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
#
# ===----------------------------------------------------------------------===

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Iterable, Tuple, Optional, Type

import requests
from platform_driver.interfaces import BaseInterface, BaseRegister, BasicRevert

_log = logging.getLogger(__name__)

type_mapping = {
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "bool": bool,
    "boolean": bool,
}

NUMERIC_POINTS = {
    "temperature",
    "heat_setpoint",
    "cool_setpoint",
    "position",
    "percentage",
    "brightness",
    "speed",
}

# =====================================================================
# Exceptions
# =====================================================================

class UnsupportedDomainError(ValueError):
    def __init__(self, entity_id: str, message: Optional[str] = None) -> None:
        self.entity_id = entity_id
        if message is None:
            message = f"Unsupported Home Assistant domain in entity_id: {entity_id!r}"
        super().__init__(message)


class UnsupportedPointError(KeyError):
    def __init__(self, point_name: str, entity_id: Optional[str] = None) -> None:
        self.point_name = point_name
        self.entity_id = entity_id
        msg = f"Unsupported point {point_name!r}"
        if entity_id is not None:
            msg += f" for entity_id {entity_id!r}"
        super().__init__(msg)


class ServiceCallError(RuntimeError):
    def __init__(self, domain: str, service: str, payload: Mapping[str, Any], message: str) -> None:
        self.domain = domain
        self.service = service
        self.payload = dict(payload)
        super().__init__(message)


# =====================================================================
# Service call structure
# =====================================================================

@dataclass(frozen=True)
class HomeAssistantServiceCall:
    domain: str
    service: str
    payload: Dict[str, Any]


# =====================================================================
# Utilities
# =====================================================================

def parse_entity_id(entity_id: str) -> Tuple[str, str]:
    if not isinstance(entity_id, str):
        raise ValueError("entity_id must be a non-empty string")

    entity_id = entity_id.strip()
    if not entity_id:
        raise ValueError("entity_id must be a non-empty string")

    parts = entity_id.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid Home Assistant entity_id: {entity_id!r}")

    return parts[0], parts[1]


def get_domain_from_entity_id(entity_id: str) -> str:
    domain, _ = parse_entity_id(entity_id)
    return domain


def _normalize_value(point_name: str, value: Any) -> Any:
    if point_name == "state":
        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("on", "true", "open", "yes", "1"):
                return True
            if lowered in ("off", "false", "closed", "close", "no", "0"):
                return False

        if isinstance(value, (int, float)):
            if value == 1:
                return True
            if value == 0:
                return False

        return value

    if point_name in NUMERIC_POINTS:
        if isinstance(value, (int, float)):
            return value

        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                try:
                    return float(stripped) if "." in stripped else int(stripped)
                except ValueError:
                    return value

        return value

    return value


def build_service_payload(entity_id: str, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    payload = {"entity_id": entity_id}
    if extra:
        for k, v in extra.items():
            if k != "entity_id":
                payload[k] = v
    return payload


# =====================================================================
# Base Domain Handler
# =====================================================================

class HomeAssistantDomainHandler(ABC):
    def __init__(self, entity_id: str, config: Mapping[str, Any]) -> None:
        self._entity_id = entity_id
        self._config = dict(config or {})

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def config(self) -> Mapping[str, Any]:
        return self._config

    @abstractmethod
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        raise NotImplementedError


# =====================================================================
# Fan Handler
# =====================================================================

class FanDomainHandler(HomeAssistantDomainHandler):
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            normalized = _normalize_value("state", value)
            if not isinstance(normalized, bool):
                raise ValueError(f"Invalid state value for fan: {value!r}")

            service = "turn_on" if normalized else "turn_off"
            return HomeAssistantServiceCall("fan", service, build_service_payload(self.entity_id))

        if point_name in ("percentage", "speed"):
            normalized = _normalize_value("percentage", value)
            try:
                percentage = int(normalized)
            except (TypeError, ValueError):
                raise ValueError(f"Fan percentage must be numeric, got {value!r}")

            if not (0 <= percentage <= 100):
                raise ValueError(f"Fan percentage must be between 0 and 100, got {percentage}")

            return HomeAssistantServiceCall(
                "fan",
                "set_percentage",
                build_service_payload(self.entity_id, {"percentage": percentage}),
            )

        raise UnsupportedPointError(point_name, self.entity_id)


# =====================================================================
# Switch Handler 
# =====================================================================

class SwitchDomainHandler(HomeAssistantDomainHandler):

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name != "state":
            raise UnsupportedPointError(point_name, self.entity_id)

        normalized = _normalize_value("state", value)
        if not isinstance(normalized, bool):
            raise ValueError(
                f"Invalid state value for switch: {value!r}. "
                "Expected a boolean-like value such as 0/1, True/False, 'on'/'off'."
            )

        service = "turn_on" if normalized else "turn_off"
        payload = build_service_payload(self.entity_id)

        return HomeAssistantServiceCall("switch", service, payload)

# =====================================================================
# Cover Handler
# =====================================================================

class CoverDomainHandler(HomeAssistantDomainHandler):
    """
    Handler for cover.* entities.

    Supported:
        - state:
            * "stop", 2 → stop_cover
            * boolean-like → open_cover / close_cover
            * "open"/"close"/"closed" as fallback
        - position: 0–100
    """

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        # -----------------------
        # STATE HANDLING
        # -----------------------
        if point_name == "state":
            raw = value

            # Explicit stop
            if isinstance(raw, str) and raw.strip().lower() == "stop":
                return HomeAssistantServiceCall(
                    "cover",
                    "stop_cover",
                    build_service_payload(self.entity_id),
                )

            if raw in (2, "2"):
                return HomeAssistantServiceCall(
                    "cover",
                    "stop_cover",
                    build_service_payload(self.entity_id),
                )

            # Boolean-like values
            normalized = _normalize_value("state", raw)
            if isinstance(normalized, bool):
                service = "open_cover" if normalized else "close_cover"
                return HomeAssistantServiceCall(
                    "cover",
                    service,
                    build_service_payload(self.entity_id),
                )

            # Fallback explicit strings
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "open":
                    return HomeAssistantServiceCall(
                        "cover",
                        "open_cover",
                        build_service_payload(self.entity_id),
                    )
                if lowered in ("close", "closed"):
                    return HomeAssistantServiceCall(
                        "cover",
                        "close_cover",
                        build_service_payload(self.entity_id),
                    )

            raise ValueError(
                f"Invalid state value for cover: {value!r}. "
                "Expected boolean-like values, 'open'/'close'/'stop', or 0/1/2 codes."
            )

        # -----------------------
        # POSITION HANDLING
        # -----------------------
        if point_name == "position":
            normalized = _normalize_value("position", value)
            try:
                pos = int(normalized)
            except (TypeError, ValueError):
                raise ValueError(f"Cover position must be numeric, got {value!r}")

            if not (0 <= pos <= 100):
                raise ValueError(f"Cover position must be between 0 and 100, got {pos}")

            return HomeAssistantServiceCall(
                "cover",
                "set_cover_position",
                build_service_payload(self.entity_id, {"position": pos}),
            )

        raise UnsupportedPointError(point_name, self.entity_id)


# =====================================================================
# Light Handler (legacy but brought into registry)
# =====================================================================

class LightHandler(HomeAssistantDomainHandler):
    """
    Handler for light.* entities.

    Supported:
        - state: boolean-like
        - brightness: 0–255
    """

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            normalized = _normalize_value("state", value)
            if not isinstance(normalized, bool):
                raise ValueError(
                    f"Invalid state value for light: {value!r}. "
                    "Expected boolean-like 0/1, True/False, 'on'/'off'."
                )

            service = "turn_on" if normalized else "turn_off"
            return HomeAssistantServiceCall(
                "light",
                service,
                build_service_payload(self.entity_id),
            )

        if point_name == "brightness":
            normalized = _normalize_value("brightness", value)
            try:
                brightness = int(normalized)
            except (TypeError, ValueError):
                raise ValueError(f"Brightness must be numeric, got {value!r}")

            if not (0 <= brightness <= 255):
                raise ValueError(f"Brightness must be between 0 and 255, got {brightness}")

            return HomeAssistantServiceCall(
                "light",
                "turn_on",   # HA sets brightness via turning on
                build_service_payload(self.entity_id, {"brightness": brightness}),
            )

        raise UnsupportedPointError(point_name, self.entity_id)


# =====================================================================
# Thermostat Handler
# =====================================================================

class ThermostatHandler(HomeAssistantDomainHandler):
    """
    climate.* handler supporting:
        - state: 0/2/3/4 or 'off'/'heat'/'cool'/'auto'
        - temperature: numeric, Fahrenheit→Celsius if units=C (legacy behavior)
    """

    MODE_CODE_TO_NAME = {0: "off", 2: "heat", 3: "cool", 4: "auto"}

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        # -----------------------
        # HVAC MODE
        # -----------------------
        if point_name == "state":
            mode_name = None

            if isinstance(value, (int, float)):
                mode_name = self.MODE_CODE_TO_NAME.get(int(value))

            if isinstance(value, str) and mode_name is None:
                lowered = value.strip().lower()
                if lowered in ("off", "heat", "cool", "auto"):
                    mode_name = lowered

            if mode_name is None:
                raise ValueError(
                    f"Invalid climate state value: {value!r}, "
                    "expected 0/2/3/4 or 'off'/'heat'/'cool'/'auto'."
                )

            return HomeAssistantServiceCall(
                "climate",
                "set_hvac_mode",
                build_service_payload(self.entity_id, {"hvac_mode": mode_name}),
            )

        # -----------------------
        # TEMPERATURE SETPOINT
        # -----------------------
        if point_name == "temperature":
            normalized = _normalize_value("temperature", value)
            try:
                temperature = float(normalized)
            except (TypeError, ValueError):
                raise ValueError(f"Temperature must be numeric, got {value!r}")

            units = self.config.get("units")
            if units == "C":
                converted = round((temperature - 32.0) * 5.0 / 9.0, 1)
            else:
                converted = temperature

            return HomeAssistantServiceCall(
                "climate",
                "set_temperature",
                build_service_payload(self.entity_id, {"temperature": converted}),
            )

        raise UnsupportedPointError(point_name, self.entity_id)


# =====================================================================
# Handler Registry
# =====================================================================

HANDLER_REGISTRY: Dict[str, Type[HomeAssistantDomainHandler]] = {
    "fan": FanDomainHandler,
    "switch": SwitchDomainHandler,
    "cover": CoverDomainHandler,
    "light": LightHandler,
    "climate": ThermostatHandler,
}


# =====================================================================
# Register Type
# =====================================================================

class HomeAssistantRegister(BaseRegister):
    def __init__(
        self,
        read_only,
        pointName,
        units,
        reg_type,
        attributes,
        entity_id,
        entity_point,
        default_value=None,
        description='',
    ):
        super(HomeAssistantRegister, self).__init__(
            "byte", read_only, pointName, units, description=""
        )
        self.reg_type = reg_type
        self.attributes = attributes
        self.entity_id = entity_id
        self.entity_point = entity_point
        self.value = None
    
# =====================================================================
# HTTP helpers
# =====================================================================

def _send_service_call(
    config: Mapping[str, Any],
    domain: str,
    service: str,
    payload: Mapping[str, Any],
) -> None:
    """
    Perform a Home Assistant service call via HTTP POST.
    """
    base_url = config.get("base_url")
    access_token = config.get("access_token")

    if not base_url:
        raise ServiceCallError(
            domain, service, payload, "Home Assistant base_url is not configured"
        )
    if not access_token:
        raise ServiceCallError(
            domain, service, payload, "Home Assistant access_token is not configured"
        )

    url = build_service_url(base_url, domain, service)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=dict(payload))
    except requests.RequestException as exc:
        msg = (
            f"Error calling {domain}.{service} "
            f"entity_id={payload.get('entity_id')!r}: {exc}"
        )
        _log.error(msg)
        raise ServiceCallError(domain, service, payload, msg)

    if not 200 <= response.status_code < 300:
        msg = (
            f"Failed to call {domain}.{service} for {payload.get('entity_id')!r}. "
            f"Status: {response.status_code}. Response: {response.text}"
        )
        _log.error(msg)
        raise ServiceCallError(domain, service, payload, msg)

    _log.info(
        "Home Assistant call OK: %s.%s(%r)", domain, service, payload.get("entity_id")
    )


def _post_method(url, headers, data, operation_description):
    """
    Thin wrapper used by legacy light/climate paths.
    """
    err = None
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            _log.info(f"Success: {operation_description}")
        else:
            err = (
                f"Failed to {operation_description}. "
                f"Status {response.status_code}. Response: {response.text}"
            )
    except requests.RequestException as e:
        err = f"Error {operation_description}: {e}"

    if err:
        _log.error(err)
        raise Exception(err)


# =====================================================================
# Main Volttron Interface
# =====================================================================

class Interface(BasicRevert, BaseInterface):
    def __init__(self, **kwargs):
        super(Interface, self).__init__(**kwargs)
        self.point_name = None
        self.ip_address: Optional[str] = None
        self.access_token: Optional[str] = None
        self.port: Optional[int] = None
        self.units = None

        self._base_url: Optional[str] = None
        self._entity_handlers: Dict[str, HomeAssistantDomainHandler] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, config_dict, registry_config_str):
        self.ip_address = config_dict.get("ip_address")
        self.access_token = config_dict.get("access_token")
        self.port = config_dict.get("port")

        if not self.ip_address:
            raise ValueError("IP address is required.")
        if not self.access_token:
            raise ValueError("Access token is required.")
        if not self.port:
            raise ValueError("Port is required.")

        self._base_url = f"http://{self.ip_address}:{self.port}"

        # registry_config_str already parsed into list of dicts
        self.parse_config(registry_config_str)

    # ------------------------------------------------------------------
    # Generic handler-based HTTP caller
    # ------------------------------------------------------------------

    def _call_service(self, service_call: HomeAssistantServiceCall, desc: str) -> None:
        _log.info("Calling HA service: %s", desc)
        config = {
            "base_url": self._base_url,
            "access_token": self.access_token,
        }
        _send_service_call(
            config,
            service_call.domain,
            service_call.service,
            service_call.payload,
        )

    # ------------------------------------------------------------------
    # Handler lookup or creation
    # ------------------------------------------------------------------

    def _get_handler_for_register(
        self,
        register: HomeAssistantRegister,
    ) -> HomeAssistantDomainHandler:
        entity_id = register.entity_id

        # cached?
        if entity_id in self._entity_handlers:
            return self._entity_handlers[entity_id]

        # determine domain
        try:
            domain = get_domain_from_entity_id(entity_id)
        except ValueError:
            raise UnsupportedDomainError(entity_id, "Invalid entity_id format")

        handler_cls = HANDLER_REGISTRY.get(domain)
        if handler_cls is None:
            raise UnsupportedDomainError(entity_id)

        handler_cfg = {
            "entity_point": register.entity_point,
            "attributes": register.attributes,
            "units": getattr(register, "units", None),
        }

        handler = handler_cls(entity_id, handler_cfg)
        self._entity_handlers[entity_id] = handler
        return handler

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------

    def get_point(self, point_name):
        register = self.get_register_by_name(point_name)
        entity_data = self.get_entity_data(register.entity_id)

        if register.point_name == "state":
            return entity_data.get("state")

        return entity_data.get("attributes", {}).get(register.point_name, 0)

    # ------------------------------------------------------------------
    # WRITE
    # ------------------------------------------------------------------

    def _set_point(self, point_name, value):
        register: HomeAssistantRegister = self.get_register_by_name(point_name)

        if register.read_only:
            raise IOError(f"Trying to write read-only point: {point_name}")

        entity_id = register.entity_id
        entity_point = register.entity_point  # actual HA field

        # handler-based domains first
        try:
            domain = get_domain_from_entity_id(entity_id)
        except ValueError:
            domain = ""

        if domain in HANDLER_REGISTRY:
            register.value = value
            handler = self._get_handler_for_register(register)

            # IMPORTANT: pass the **entity_point** from registry
            service_call = handler.build_service_call(entity_point, register.value)

            desc = f"{service_call.domain}.{service_call.service} for {entity_id}"
            self._call_service(service_call, desc)
            return register.value

        # ------------------------------------------------------------------
        # Legacy path for light.*, input_boolean.*, climate.* without handler
        # ------------------------------------------------------------------

        register.value = register.reg_type(value)

        # LIGHTS
        if entity_id.startswith("light."):
            if entity_point == "state":
                if register.value == 1:
                    self.turn_on_lights(entity_id)
                elif register.value == 0:
                    self.turn_off_lights(entity_id)
                else:
                    raise ValueError(f"State for {entity_id} must be 0 or 1")

            elif entity_point == "brightness":
                if isinstance(register.value, int) and 0 <= register.value <= 255:
                    self.change_brightness(entity_id, register.value)
                else:
                    raise ValueError("Brightness must be int 0–255")

            else:
                raise ValueError(
                    f"Unsupported point {entity_point} for {entity_id}. "
                    "Lights support state/brightness."
                )

        # INPUT BOOLEAN
        elif entity_id.startswith("input_boolean."):
            if entity_point == "state":
                if register.value in ["on", "off"]:
                    self.set_input_boolean(entity_id, register.value)
                else:
                    raise ValueError("input_boolean state must be 'on' or 'off'")
            else:
                raise ValueError(
                    f"Unsupported point {entity_point} for {entity_id}. "
                    "input_boolean only supports state."
                )

        # CLIMATE
        elif entity_id.startswith("climate."):
            if entity_point == "state":
                mode_map = {0: "off", 2: "heat", 3: "cool", 4: "auto"}
                mode = mode_map.get(register.value)
                if not mode:
                    raise ValueError("Climate state must be 0/2/3/4")
                self.change_thermostat_mode(entity_id, mode)

            elif entity_point == "temperature":
                if isinstance(register.value, (int, float)):
                    self.set_thermostat_temperature(entity_id, register.value)
                else:
                    raise ValueError("Temperature must be numeric")

            else:
                raise ValueError(
                    f"Unsupported climate point {entity_point} for {entity_id}"
                )

        else:
            raise ValueError(
                f"Unsupported entity_id {entity_id}. Only lights/thermostats "
                "supported in legacy mode."
            )

        return register.value

    # ------------------------------------------------------------------
    # Entity data helpers (READ path)
    # ------------------------------------------------------------------

    def get_entity_data(self, entity_id):
        """
        Fetch current state and attributes for a specific entity from Home Assistant.
        """
        if not self._base_url:
            raise RuntimeError("Home Assistant base URL is not configured.")

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/api/states/{entity_id}"

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            msg = (
                f"Request failed with status {response.status_code} for {entity_id}. "
                f"Response: {response.text}"
            )
            _log.error(msg)
            raise Exception(msg)

    def _scrape_all(self):
        """
        Bulk read of all registers.

        NOTE:
            Still uses the original climate/light/input_boolean logic.
            Other entities just expose raw state/attributes.
        """
        result: Dict[str, Any] = {}
        read_registers = self.get_registers_by_type("byte", True)
        write_registers = self.get_registers_by_type("byte", False)

        for register in read_registers + write_registers:
            entity_id = register.entity_id
            entity_point = register.entity_point
            try:
                entity_data = self.get_entity_data(entity_id)

                # Thermostats
                if entity_id.startswith("climate."):
                    if entity_point == "state":
                        state = entity_data.get("state")
                        if state == "off":
                            register.value = 0
                        elif state == "heat":
                            register.value = 2
                        elif state == "cool":
                            register.value = 3
                        elif state == "auto":
                            register.value = 4
                        else:
                            _log.error(
                                "Unsupported climate state %r from %s", state, entity_id
                            )
                            continue
                        result[register.point_name] = register.value
                    else:
                        attr = entity_data.get("attributes", {}).get(entity_point, 0)
                        register.value = attr
                        result[register.point_name] = attr

                # Lights + input_boolean
                elif entity_id.startswith("light.") or entity_id.startswith(
                    "input_boolean."
                ):
                    if entity_point == "state":
                        state = entity_data.get("state")
                        if state == "on":
                            register.value = 1
                        elif state == "off":
                            register.value = 0
                        else:
                            _log.error(
                                "Unsupported on/off state %r from %s", state, entity_id
                            )
                            continue
                        result[register.point_name] = register.value
                    else:
                        attr = entity_data.get("attributes", {}).get(entity_point, 0)
                        register.value = attr
                        result[register.point_name] = attr

                # Generic entities
                else:
                    if entity_point == "state":
                        state = entity_data.get("state")
                        register.value = state
                        result[register.point_name] = state
                    else:
                        attr = entity_data.get("attributes", {}).get(entity_point, 0)
                        register.value = attr
                        result[register.point_name] = attr

            except Exception as exc:
                _log.error(
                    "Error scraping entity_id %s for point %s: %s",
                    entity_id,
                    register.point_name,
                    exc,
                )

        return result

    # ------------------------------------------------------------------
    # Registry parsing
    # ------------------------------------------------------------------

    def parse_config(self, config_dict):
        """
        Parse the registry configuration into HomeAssistantRegister instances.

        After creating each register, we also attempt to attach a domain
        handler (fan/switch/cover/light/climate) based on its entity_id.
        """
        if not config_dict:
            return

        for reg_def in config_dict:
            if not reg_def.get("Entity ID"):
                continue

            read_only = str(reg_def.get("Writable", "")).lower() != "true"
            entity_id = reg_def["Entity ID"]
            entity_point = reg_def["Entity Point"]
            self.point_name = reg_def["Volttron Point Name"]
            self.units = reg_def["Units"]
            description = reg_def.get("Notes", "")
            default_value = "Starting Value"

            type_name = reg_def.get("Type", "string")
            reg_type = type_mapping.get(type_name, str)
            attributes = reg_def.get("Attributes", {}) or {}

            register = HomeAssistantRegister(
                read_only=read_only,
                pointName=self.point_name,
                units=self.units,
                reg_type=reg_type,
                attributes=attributes,
                entity_id=entity_id,
                entity_point=entity_point,
                default_value=default_value,
                description=description,
            )

            if default_value is not None:
                self.set_default(self.point_name, register.value)

            self.insert_register(register)

            # try to pre-create a domain handler if supported
            try:
                domain = get_domain_from_entity_id(entity_id)
            except ValueError:
                _log.warning(
                    "Skipping handler registration, invalid entity_id: %r", entity_id
                )
                continue

            handler_cls = HANDLER_REGISTRY.get(domain)
            if handler_cls and entity_id not in self._entity_handlers:
                handler_cfg = {
                    "entity_point": entity_point,
                    "attributes": attributes,
                    "units": self.units,
                }
                self._entity_handlers[entity_id] = handler_cls(entity_id, handler_cfg)

    # ------------------------------------------------------------------
    # Legacy convenience helpers (lights / climate / input_boolean)
    # ------------------------------------------------------------------

    def turn_off_lights(self, entity_id):
        url = f"{self._base_url}/api/services/light/turn_off"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {"entity_id": entity_id}
        _post_method(url, headers, payload, f"turn off {entity_id}")

    def turn_on_lights(self, entity_id):
        url = f"{self._base_url}/api/services/light/turn_on"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {"entity_id": entity_id}
        _post_method(url, headers, payload, f"turn on {entity_id}")

    def change_thermostat_mode(self, entity_id, mode):
        if not entity_id.startswith("climate."):
            _log.error("%s is not a thermostat entity_id", entity_id)
            return

        url = f"{self._base_url}/api/services/climate/set_hvac_mode"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        data = {
            "entity_id": entity_id,
            "hvac_mode": mode,
        }
        _post_method(url, headers, data, f"set hvac_mode of {entity_id} to {mode}")

    def set_thermostat_temperature(self, entity_id, temperature):
        if not entity_id.startswith("climate."):
            _log.error("%s is not a thermostat entity_id", entity_id)
            return

        url = f"{self._base_url}/api/services/climate/set_temperature"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        if self.units == "C":
            converted = round((temperature - 32) * 5.0 / 9.0, 1)
            _log.info("Converted temperature %s°F -> %s°C", temperature, converted)
            data = {"entity_id": entity_id, "temperature": converted}
        else:
            data = {"entity_id": entity_id, "temperature": temperature}

        _post_method(
            url,
            headers,
            data,
            f"set temperature of {entity_id} to {temperature}",
        )

    def change_brightness(self, entity_id, value):
        url = f"{self._base_url}/api/services/light/turn_on"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "entity_id": entity_id,
            "brightness": value,
        }
        _post_method(
            url,
            headers,
            payload,
            f"set brightness of {entity_id} to {value}",
        )

    def set_input_boolean(self, entity_id, state):
        service = "turn_on" if state == "on" else "turn_off"
        url = f"{self._base_url}/api/services/input_boolean/{service}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {"entity_id": entity_id}

        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            _log.info("Successfully set %s to %s", entity_id, state)
        else:
            _log.error(
                "Failed to set %s to %s: %s", entity_id, state, response.text
            )