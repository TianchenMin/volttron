# -*- coding: utf-8 -*- {{{
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
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Iterable, Tuple, Optional, Type

from platform_driver.interfaces import BaseInterface, BaseRegister, BasicRevert
import requests

_log = logging.getLogger(__name__)

type_mapping = {
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "bool": bool,
    "boolean": bool,
}

# =====================================================================
# Exceptions
# =====================================================================


class UnsupportedDomainError(ValueError):
    """
    Raised when a Home Assistant entity uses a domain that is not supported
    by this driver (i.e., no handler is registered for that domain).
    """

    def __init__(self, entity_id: str, message: Optional[str] = None) -> None:
        self.entity_id = entity_id
        if message is None:
            message = f"Unsupported Home Assistant domain in entity_id: {entity_id!r}"
        super().__init__(message)


class UnsupportedPointError(KeyError):
    """
    Raised when a handler is asked to read/write a point that it does not
    know how to map to a Home Assistant service or attribute.
    """

    def __init__(self, point_name: str, entity_id: Optional[str] = None) -> None:
        self.point_name = point_name
        self.entity_id = entity_id
        msg = f"Unsupported point {point_name!r}"
        if entity_id is not None:
            msg += f" for entity_id {entity_id!r}"
        super().__init__(msg)


class ServiceCallError(RuntimeError):
    """
    Raised when a service call to Home Assistant fails at the transport
    or protocol level (e.g., HTTP error status, connection failure, etc.).

    NOTE:
        This error is used by the generic service-calling path in this module.
        Older helper methods may still raise a generic Exception; new code
        should prefer raising ServiceCallError instead.
    """

    def __init__(
        self,
        domain: str,
        service: str,
        payload: Mapping[str, Any],
        message: str,
    ) -> None:
        self.domain = domain
        self.service = service
        self.payload = dict(payload)
        super().__init__(message)


# =====================================================================
# Service call description
# =====================================================================


@dataclass(frozen=True)
class HomeAssistantServiceCall:
    """
    Immutable description of a Home Assistant service call.

    Instances of this class are produced by HomeAssistantDomainHandler.build_service_call
    and consumed by higher-level driver code that actually performs HTTP requests
    against the Home Assistant HTTP API.

    IMPORTANT:
        This object is *pure data* – creating it must not perform any network I/O.
    """

    domain: str
    service: str
    payload: Dict[str, Any]


# =====================================================================
# Utility functions (pure, no HTTP)
# =====================================================================


def parse_entity_id(entity_id: str) -> Tuple[str, str]:
    """
    Split a Home Assistant entity_id into (domain, object_id).

    Example:
        "fan.living_room_fan" -> ("fan", "living_room_fan")

    Raises:
        ValueError: if the entity_id does not contain a '.' separator.
    """
    parts = entity_id.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid Home Assistant entity_id: {entity_id!r}")
    return parts[0], parts[1]


def ensure_supported_domain(entity_id: str, supported_domains: Iterable[str]) -> str:
    """
    Validate that the entity_id's domain is within the given supported domains.

    Args:
        entity_id: Full Home Assistant entity_id, e.g. "fan.living_room_fan".
        supported_domains: Iterable of supported domain strings.

    Returns:
        The domain component of the entity_id if it is supported.

    Raises:
        UnsupportedDomainError: if the domain is not in supported_domains.
    """
    domain, _ = parse_entity_id(entity_id)
    if domain not in supported_domains:
        raise UnsupportedDomainError(entity_id)
    return domain


def build_service_url(base_url: str, domain: str, service: str) -> str:
    """
    Build the Home Assistant service API URL from the base_url, domain, and service.

    This function does not perform any network I/O. It only concatenates
    the proper API path.

    Example:
        base_url = "http://homeassistant.local:8123"
        domain   = "fan"
        service  = "turn_on"

        -> "http://homeassistant.local:8123/api/services/fan/turn_on"
    """
    base = base_url.rstrip("/")
    return f"{base}/api/services/{domain}/{service}"


def build_service_payload(
    entity_id: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Construct a payload dict for a Home Assistant service call.

    The resulting payload always contains the `entity_id` key and merges any
    additional fields from `extra`. Values in `extra` override other keys
    if there is a collision (except `entity_id`, which is enforced).

    Example:
        build_service_payload("fan.living_room_fan", {"percentage": 75})
        -> {"entity_id": "fan.living_room_fan", "percentage": 75}
    """
    payload: Dict[str, Any] = {"entity_id": entity_id}
    if extra:
        for key, value in extra.items():
            if key == "entity_id":
                continue
            payload[key] = value
    return payload


def get_domain_from_entity_id(entity_id: str) -> str:
    """
    Extract the domain from a Home Assistant entity_id.

    Example:
        "fan.living_room_fan" -> "fan"

    Raises:
        ValueError: if the entity_id is malformed.
    """
    domain, _ = parse_entity_id(entity_id)
    return domain


def _normalize_value(point_name: str, value: Any) -> Any:
    """
    Normalize raw input values into reasonable internal types.

    Team-wide conventions:

    - point_name == "state":
        Truthy ON values:
            "on", "ON", "true", "True", "yes", "YES", True, 1, "1"
            -> True
        Falsy OFF values:
            "off", "OFF", "false", "False", "no", "NO", False, 0, "0"
            -> False
        Other values -> ValueError

    - Numeric points (temperature, position, percentage, speed, etc.):
        - int/float: keep as-is
        - str: try float(value)
        - other/failed conversion: ValueError

    - Other point names:
        - Return value as-is.
    """
    if point_name == "state":
        # Already bool
        if isinstance(value, bool):
            return value

        # Integers
        if isinstance(value, int):
            if value == 1:
                return True
            if value == 0:
                return False

        # Strings
        if isinstance(value, str):
            lower = value.strip().lower()
            if lower in ("on", "true", "yes", "1"):
                return True
            if lower in ("off", "false", "no", "0"):
                return False

        raise ValueError(f"Cannot normalize state value: {value!r}")

    numeric_points = {
        "temperature",
        "heat_setpoint",
        "cool_setpoint",
        "position",
        "percentage",
        "speed",
    }

    if point_name in numeric_points:
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                raise ValueError(
                    f"Cannot normalize numeric value for {point_name!r}: {value!r}"
                )
        raise ValueError(
            f"Unsupported type for numeric point {point_name!r}: "
            f"{type(value).__name__}"
        )

    # Default: leave untouched
    return value


# =====================================================================
# Base handler for domains (fan / switch / cover / ...)
# =====================================================================


class HomeAssistantDomainHandler(ABC):
    """
    Base class for per-domain Home Assistant handlers (fan, switch, cover, etc.).

    Responsibilities:
        - Hold common metadata such as `entity_id` and handler-specific config.
        - Provide a stable abstract method `build_service_call(...)` that
          subclasses implement to map logical "points" to Home Assistant
          (domain, service, payload).

    IMPORTANT:
        Subclasses are ONLY responsible for deciding `(domain, service, payload)`
        for a given `point_name` and `value`. They MUST NOT perform any HTTP
        requests or other network I/O directly. The actual HTTP call will be
        done by higher-level driver code using the returned HomeAssistantServiceCall.
    """

    def __init__(self, entity_id: str, config: Mapping[str, Any]) -> None:
        """
        Initialize a domain handler.

        Args:
            entity_id:
                Full Home Assistant entity_id, e.g. "fan.living_room_fan".
            config:
                Per-entity or per-handler configuration dictionary.
                The base class stores a shallow copy so callers retain ownership.
        """
        self._entity_id: str = entity_id
        self._config: Dict[str, Any] = dict(config or {})

    @property
    def entity_id(self) -> str:
        """Return the full Home Assistant entity_id for this handler."""
        return self._entity_id

    @property
    def config(self) -> Mapping[str, Any]:
        """
        Read-only view of the handler configuration.

        Subclasses may inspect configuration but should avoid mutating it
        in-place. If mutation is required, they should work on a local copy.
        """
        return self._config

    @abstractmethod
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        """
        Build a HomeAssistantServiceCall for a given logical point.

        Args:
            point_name:
                Logical "point" name as used by the Volttron driver
                (e.g., "on", "off", "level").
            value:
                Value to write for this point. Different handlers may accept
                different value types (bool, int, str, enums, etc.).

        Returns:
            A HomeAssistantServiceCall object containing:
                - domain:  Home Assistant domain (e.g. "fan", "switch", "cover")
                - service: Home Assistant service name within that domain
                           (e.g. "turn_on", "turn_off", "set_percentage")
                - payload: JSON-serializable dict to send in the HTTP body,
                           which MUST include at least the `entity_id`.

        Raises:
            UnsupportedPointError:
                If the given `point_name` is not supported by this handler.

        IMPORTANT:
            This method MUST NOT perform any HTTP or network I/O.
        """
        raise NotImplementedError


# =====================================================================
# Domain handler implementations
# =====================================================================


class FanDomainHandler(HomeAssistantDomainHandler):
    """
    Handler for `fan.*` entities.

    Supported points:
        - "state": bool-like value -> fan.turn_on / fan.turn_off
        - "percentage" / "speed": numeric 0-100 -> fan.set_percentage

    NOTE:
        All input values are normalized via _normalize_value before use.
    """

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            normalized = _normalize_value("state", value)
            if normalized is True:
                service = "turn_on"
            elif normalized is False:
                service = "turn_off"
            else:
                # _normalize_value should never return non-bool here
                raise ValueError(f"Unexpected normalized state for fan: {normalized!r}")

            payload = build_service_payload(self.entity_id)
            return HomeAssistantServiceCall(
                domain="fan",
                service=service,
                payload=payload,
            )

        elif point_name in ("percentage", "speed"):
            normalized = _normalize_value("percentage", value)
            percentage = int(normalized)
            if not (0 <= percentage <= 100):
                raise ValueError(
                    f"Fan percentage must be between 0 and 100, got {percentage}"
                )

            payload = build_service_payload(self.entity_id, {"percentage": percentage})
            return HomeAssistantServiceCall(
                domain="fan",
                service="set_percentage",
                payload=payload,
            )

        raise UnsupportedPointError(point_name, self.entity_id)


class SwitchDomainHandler(HomeAssistantDomainHandler):
    """
    Handler for `switch.*` entities.

    Supported points:
        - "state": bool-like value -> switch.turn_on / switch.turn_off
    """

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            normalized = _normalize_value("state", value)
            if normalized is True:
                service = "turn_on"
            elif normalized is False:
                service = "turn_off"
            else:
                raise ValueError(
                    f"Unexpected normalized state for switch: {normalized!r}"
                )

            payload = build_service_payload(self.entity_id)
            return HomeAssistantServiceCall(
                domain="switch",
                service=service,
                payload=payload,
            )

        raise UnsupportedPointError(point_name, self.entity_id)


class CoverDomainHandler(HomeAssistantDomainHandler):
    """
    Handler for `cover.*` entities (e.g. blinds, shades, garage doors).

    Supported points:
        - "state":
            * Boolean-like values (on/off/true/false/1/0/yes/no) via _normalize_value("state")
              mapped to open/close.
            * Explicit "open"/"close"/"stop" and 0/1/2 codes also supported.
        - "position": numeric 0-100 -> cover.set_cover_position
    """

    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            raw = value

            # First handle explicit "stop"
            if isinstance(raw, str) and raw.strip().lower() == "stop":
                payload = build_service_payload(self.entity_id)
                return HomeAssistantServiceCall(
                    domain="cover",
                    service="stop_cover",
                    payload=payload,
                )

            # Then normalize to boolean using _normalize_value("state")
            try:
                normalized = _normalize_value("state", raw)
            except ValueError:
                # Fallback: still allow "open"/"close" and 0/1/2 codes
                # (open = True, close = False)
                if isinstance(raw, str):
                    lowered = raw.strip().lower()
                    if lowered == "open":
                        normalized = True
                    elif lowered == "close":
                        normalized = False
                    elif lowered == "stop":
                        # We already handled explicit "stop" above, but keep this
                        # in case someone passes a different-cased string.
                        payload = build_service_payload(self.entity_id)
                        return HomeAssistantServiceCall(
                            domain="cover",
                            service="stop_cover",
                            payload=payload,
                        )
                    else:
                        raise
                elif raw in (0, 1, "0", "1"):
                    normalized = int(raw) == 1
                elif raw in (2, "2"):
                    payload = build_service_payload(self.entity_id)
                    return HomeAssistantServiceCall(
                        domain="cover",
                        service="stop_cover",
                        payload=payload,
                    )
                else:
                    raise

            service = "open_cover" if normalized else "close_cover"
            payload = build_service_payload(self.entity_id)
            return HomeAssistantServiceCall(
                domain="cover",
                service=service,
                payload=payload,
            )

        elif point_name == "position":
            normalized = _normalize_value("position", value)
            position = int(normalized)
            if not (0 <= position <= 100):
                raise ValueError(
                    f"Cover position must be between 0 and 100, got {position}"
                )

            payload = build_service_payload(self.entity_id, {"position": position})
            return HomeAssistantServiceCall(
                domain="cover",
                service="set_cover_position",
                payload=payload,
            )

        raise UnsupportedPointError(point_name, self.entity_id)


# Domain -> handler class registry. Extend this mapping when adding new domains.
DOMAIN_HANDLERS: Dict[str, Type[HomeAssistantDomainHandler]] = {
    "fan": FanDomainHandler,
    "switch": SwitchDomainHandler,
    "cover": CoverDomainHandler,
}


# =====================================================================
# Register type
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
            "byte", read_only, pointName, units, description=''
        )
        self.reg_type = reg_type
        self.attributes = attributes
        self.entity_id = entity_id
        self.value = None
        self.entity_point = entity_point


# =====================================================================
# Legacy helper for simple POSTs (still used by some old methods)
# =====================================================================


def _send_service_call(
    config: Mapping[str, Any],
    domain: str,
    service: str,
    payload: Mapping[str, Any],
) -> None:
    """
    Perform a Home Assistant service call via HTTP POST.

    Args:
        config:
            Mapping containing at least:
                - "base_url": e.g. "http://192.168.1.10:8123"
                - "access_token": Home Assistant long-lived access token.
        domain:
            Home Assistant domain (e.g. "fan", "switch", "cover").
        service:
            Service name within the domain (e.g. "turn_on", "set_percentage").
        payload:
            JSON-serializable request body. Must include "entity_id".

    Raises:
        ServiceCallError: on network errors or non-2xx HTTP status.
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
            f"Error when attempting service call {domain}.{service} "
            f"for entity_id={payload.get('entity_id')!r}: {exc}"
        )
        _log.error(msg)
        raise ServiceCallError(domain, service, payload, msg)

    if not (200 <= response.status_code < 300):
        msg = (
            f"Failed to call {domain}.{service} for entity_id={payload.get('entity_id')!r}. "
            f"Status code: {response.status_code}. Response: {response.text}"
        )
        _log.error(msg)
        raise ServiceCallError(domain, service, payload, msg)

    _log.info(
        "Success calling %s.%s for entity_id=%r",
        domain,
        service,
        payload.get("entity_id"),
    )


def _post_method(url, headers, data, operation_description):
    err = None
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            _log.info(f"Success: {operation_description}")
        else:
            err = (
                f"Failed to {operation_description}. "
                f"Status code: {response.status_code}. Response: {response.text}"
            )
    except requests.RequestException as e:
        err = f"Error when attempting - {operation_description} : {e}"

    if err:
        _log.error(err)
        # For legacy paths we still raise a generic Exception;
        # new code should prefer ServiceCallError.
        raise Exception(err)


# =====================================================================
# Main Volttron interface
# =====================================================================


class Interface(BasicRevert, BaseInterface):
    def __init__(self, **kwargs):
        super(Interface, self).__init__(**kwargs)
        self.point_name = None
        self.ip_address: Optional[str] = None
        self.access_token: Optional[str] = None
        self.port: Optional[int] = None
        self.units = None

        # Base URL for Home Assistant, e.g. "http://192.168.1.10:8123"
        self._base_url: Optional[str] = None

        # Cache of entity_id -> domain handler instance
        self._entity_handlers: Dict[str, HomeAssistantDomainHandler] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, config_dict, registry_config_str):
        self.ip_address = config_dict.get("ip_address", None)
        self.access_token = config_dict.get("access_token", None)
        self.port = config_dict.get("port", None)

        # Check for None values
        if self.ip_address is None:
            _log.error("IP address is not set.")
            raise ValueError("IP address is required.")
        if self.access_token is None:
            _log.error("Access token is not set.")
            raise ValueError("Access token is required.")
        if self.port is None:
            _log.error("Port is not set.")
            raise ValueError("Port is required.")

        self._base_url = f"http://{self.ip_address}:{self.port}"

        # registry_config_str is already parsed into a list of register dicts
        self.parse_config(registry_config_str)

    # ------------------------------------------------------------------
    # Generic helper for calling Home Assistant services (new path)
    # ------------------------------------------------------------------

    def _call_service(
        self, service_call: HomeAssistantServiceCall, operation_description: str
    ) -> None:
        """
        Perform the HTTP POST to Home Assistant for a given service call.

        All HTTP / network details are centralized in _send_service_call(...)
        so that all write paths share the same error handling and logging
        behavior. Domain handlers remain pure and only return data objects.
        """
        config = {
            "base_url": self._base_url,
            "access_token": self.access_token,
        }
        _log.info("Calling Home Assistant service: %s", operation_description)
        _send_service_call(
            config,
            service_call.domain,
            service_call.service,
            service_call.payload,
        )

    def _get_handler_for_register(
        self, register: HomeAssistantRegister
    ) -> HomeAssistantDomainHandler:
        """
        Return a domain handler instance for the given register's entity_id.

        - If a handler instance already exists, reuse it.
        - If the entity's domain has a registered handler class, instantiate it.
        - Otherwise, raise UnsupportedDomainError.
        """
        entity_id = register.entity_id
        handler = self._entity_handlers.get(entity_id)
        if handler is not None:
            return handler

        try:
            domain = get_domain_from_entity_id(entity_id)
        except ValueError:
            # 明确说明 entity_id 格式错误
            raise UnsupportedDomainError(entity_id)

        handler_cls = DOMAIN_HANDLERS.get(domain)
        if handler_cls is None:
            # No handler registered for this domain
            raise UnsupportedDomainError(entity_id)

        handler_config = {
            "entity_point": register.entity_point,
            "attributes": register.attributes,
            "units": getattr(register, "units", None),
        }
        handler = handler_cls(entity_id, handler_config)
        self._entity_handlers[entity_id] = handler
        return handler

    # ------------------------------------------------------------------
    # Point read/write
    # ------------------------------------------------------------------

    def get_point(self, point_name):
        register = self.get_register_by_name(point_name)
        entity_data = self.get_entity_data(register.entity_id)

        if register.point_name == "state":
            result = entity_data.get("state", None)
            return result
        else:
            value = entity_data.get("attributes", {}).get(f"{register.point_name}", 0)
            return value

    def _set_point(self, point_name, value):
        """
        Write a point.

        Flow:
            - Lookup register by Volttron point name.
            - Enforce read_only flag.
            - If the entity's domain has a handler (fan/switch/cover), route
              through the new handler-based path, letting the handler +
              _normalize_value perform any value normalization.
            - Otherwise, fall back to the legacy light / input_boolean /
              climate logic and cast to the register's declared type there.
            - Cache the written value in the register.
        """
        register: HomeAssistantRegister = self.get_register_by_name(point_name)

        if register.read_only:
            raise IOError("Trying to write to a point configured read only: " + point_name)

        entity_id = register.entity_id
        entity_point = register.entity_point

        # -----------------------------
        # Handler-based domains first
        # -----------------------------
        try:
            domain = get_domain_from_entity_id(entity_id)
        except ValueError:
            domain = ""

        if domain in DOMAIN_HANDLERS:
            # For handler domains, keep the original user input and let the handler
            # + _normalize_value do the work.
            register.value = value

            handler = self._get_handler_for_register(register)
            # IMPORTANT:
            #   这里传入 handler 的是 registry 里的 Entity Point（"state"/"percentage"/"position"），
            #   而不是 Volttron Point Name。
            service_call = handler.build_service_call(entity_point, register.value)
            op_desc = f"{service_call.domain}.{service_call.service} for {entity_id}"
            self._call_service(service_call, op_desc)
            return register.value

        # -----------------------------
        # Legacy path：老逻辑才做类型转换
        # -----------------------------
        register.value = register.reg_type(value)

        # Changing lights values in Home Assistant based off of register value.
        if entity_id.startswith("light."):
            ...
        elif entity_id.startswith("input_boolean."):
            ...
        elif entity_id.startswith("climate."):
            ...
        else:
            error_msg = (
                f"Unsupported entity_id: {entity_id}. "
                f"Currently set_point is supported only for thermostats and lights"
            )
            _log.error(error_msg)
            raise ValueError(error_msg)

        return register.value

    # ------------------------------------------------------------------
    # Entity data helpers (read path, mostly legacy but still useful)
    # ------------------------------------------------------------------

    def get_entity_data(self, entity_id):
        """
        Fetch current state and attributes for a specific entity from Home Assistant.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        if not self._base_url:
            raise RuntimeError("Home Assistant base URL is not configured.")

        url = f"{self._base_url}/api/states/{entity_id}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()  # return the json attributes from entity
        else:
            error_msg = (
                f"Request failed with status code {response.status_code}, "
                f"Entity ID: {entity_id}, response: {response.text}"
            )
            _log.error(error_msg)
            raise Exception(error_msg)

    def _scrape_all(self):
        """
        Bulk read of all registers.

        NOTE:
            这里的逻辑主要是原始实现，仍然针对 climate / light / input_boolean。
            未来如果需要 fan/switch/cover 的更精细映射，可以考虑类似写 path，
            也拆成 per-domain handler。
        """
        result = {}
        read_registers = self.get_registers_by_type("byte", True)
        write_registers = self.get_registers_by_type("byte", False)

        for register in read_registers + write_registers:
            entity_id = register.entity_id
            entity_point = register.entity_point
            try:
                entity_data = self.get_entity_data(entity_id)  # Using Entity ID to get data

                # handling thermostats
                if entity_id.startswith("climate."):
                    if entity_point == "state":
                        state = entity_data.get("state", None)
                        # Giving thermostat states an equivalent number.
                        if state == "off":
                            register.value = 0
                            result[register.point_name] = 0
                        elif state == "heat":
                            register.value = 2
                            result[register.point_name] = 2
                        elif state == "cool":
                            register.value = 3
                            result[register.point_name] = 3
                        elif state == "auto":
                            register.value = 4
                            result[register.point_name] = 4
                        else:
                            error_msg = f"State {state} from {entity_id} is not yet supported"
                            _log.error(error_msg)
                            ValueError(error_msg)
                    else:
                        # Assign attribute
                        attribute = entity_data.get("attributes", {}).get(f"{entity_point}", 0)
                        register.value = attribute
                        result[register.point_name] = attribute

                # handling light & input_boolean states
                elif entity_id.startswith("light.") or entity_id.startswith(
                    "input_boolean."
                ):
                    if entity_point == "state":
                        state = entity_data.get("state", None)
                        # Converting light/input_boolean states to numbers.
                        if state == "on":
                            register.value = 1
                            result[register.point_name] = 1
                        elif state == "off":
                            register.value = 0
                            result[register.point_name] = 0
                    else:
                        attribute = entity_data.get("attributes", {}).get(f"{entity_point}", 0)
                        register.value = attribute
                        result[register.point_name] = attribute

                else:
                    # handling all devices that are not thermostats or light/input_boolean
                    if entity_point == "state":
                        state = entity_data.get("state", None)
                        register.value = state
                        result[register.point_name] = state
                    else:
                        attribute = entity_data.get("attributes", {}).get(f"{entity_point}", 0)
                        register.value = attribute
                        result[register.point_name] = attribute

            except Exception as e:
                _log.error(f"An unexpected error occurred for entity_id: {entity_id}: {e}")

        return result

    # ------------------------------------------------------------------
    # Registry parsing
    # ------------------------------------------------------------------

    def parse_config(self, config_dict):
        """
        Parse the registry configuration into HomeAssistantRegister instances.

        NOTE:
            这里保留了原来的解析逻辑，只在每个 register 创建之后，
            根据 entity_id 的 domain 尝试注册对应的 handler（fan/switch/cover）。
        """
        if config_dict is None:
            return

        for regDef in config_dict:
            if not regDef.get('Entity ID'):
                continue

            read_only = str(regDef.get('Writable', '')).lower() != 'true'
            entity_id = regDef['Entity ID']
            entity_point = regDef['Entity Point']
            self.point_name = regDef['Volttron Point Name']
            self.units = regDef['Units']
            description = regDef.get('Notes', '')
            default_value = "Starting Value"
            type_name = regDef.get("Type", 'string')
            reg_type = type_mapping.get(type_name, str)
            attributes = regDef.get('Attributes', {})
            register_type = HomeAssistantRegister

            register = register_type(
                read_only,
                self.point_name,
                self.units,
                reg_type,
                attributes,
                entity_id,
                entity_point,
                default_value=default_value,
                description=description,
            )

            if default_value is not None:
                self.set_default(self.point_name, register.value)

            self.insert_register(register)

            # 尝试为该 entity 注册 domain handler（如果是我们支持的 fan/switch/cover）
            try:
                domain = get_domain_from_entity_id(entity_id)
            except ValueError:
                _log.warning(
                    "Skipping handler registration, invalid entity_id: %r", entity_id
                )
                continue

            handler_cls = DOMAIN_HANDLERS.get(domain)
            if handler_cls is not None and entity_id not in self._entity_handlers:
                handler_config = {
                    "entity_point": entity_point,
                    "attributes": attributes,
                    "units": self.units,
                }
                self._entity_handlers[entity_id] = handler_cls(entity_id, handler_config)

    # ------------------------------------------------------------------
    # Legacy specific helpers (still usable if you keep lights/climate)
    # ------------------------------------------------------------------

    def turn_off_lights(self, entity_id):
        url = f"{self._base_url}/api/services/light/turn_off"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "entity_id": entity_id,
        }
        _post_method(url, headers, payload, f"turn off {entity_id}")

    def turn_on_lights(self, entity_id):
        url = f"{self._base_url}/api/services/light/turn_on"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "entity_id": f"{entity_id}"
        }
        _post_method(url, headers, payload, f"turn on {entity_id}")

    def change_thermostat_mode(self, entity_id, mode):
        # Check if enttiy_id startswith climate.
        if not entity_id.startswith("climate."):
            _log.error(f"{entity_id} is not a valid thermostat entity ID.")
            return

        url = f"{self._base_url}/api/services/climate/set_hvac_mode"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "content-type": "application/json",
        }
        data = {
            "entity_id": entity_id,
            "hvac_mode": mode,
        }
        _post_method(url, headers, data, f"change mode of {entity_id} to {mode}")

    def set_thermostat_temperature(self, entity_id, temperature):
        # Check if the provided entity_id starts with "climate."
        if not entity_id.startswith("climate."):
            _log.error(f"{entity_id} is not a valid thermostat entity ID.")
            return

        url = f"{self._base_url}/api/services/climate/set_temperature"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "content-type": "application/json",
        }

        if self.units == "C":
            converted_temp = round((temperature - 32) * 5 / 9, 1)
            _log.info(f"Converted temperature {converted_temp}")
            data = {
                "entity_id": entity_id,
                "temperature": converted_temp,
            }
        else:
            data = {
                "entity_id": entity_id,
                "temperature": temperature,
            }
        _post_method(
            url, headers, data, f"set temperature of {entity_id} to {temperature}"
        )

    def change_brightness(self, entity_id, value):
        url = f"{self._base_url}/api/services/light/turn_on"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        # ranges from 0 - 255
        payload = {
            "entity_id": f"{entity_id}",
            "brightness": value,
        }

        _post_method(
            url, headers, payload, f"set brightness of {entity_id} to {value}"
        )

    def set_input_boolean(self, entity_id, state):
        service = 'turn_on' if state == 'on' else 'turn_off'
        url = f"{self._base_url}/api/services/input_boolean/{service}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "entity_id": entity_id
        }

        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            _log.info(f"Successfully set {entity_id} to {state}")
        else:
            _log.error(f"Failed to set {entity_id} to {state}: {response.text}")
