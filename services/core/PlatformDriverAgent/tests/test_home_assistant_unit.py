import math
import pytest
import requests

from platform_driver.interfaces.home_assistant import (
    parse_entity_id,
    get_domain_from_entity_id,
    build_service_payload,
    _normalize_value,
    FanDomainHandler,
    SwitchDomainHandler,
    CoverDomainHandler,
    LightHandler,
    ThermostatHandler,
    HomeAssistantServiceCall,
    UnsupportedPointError,
    _send_service_call,
    ServiceCallError,
)

# Aliases for shorter names in tests
FanHandler = FanDomainHandler
SwitchHandler = SwitchDomainHandler
CoverHandler = CoverDomainHandler


# =====================================================================
# Utility function tests
# =====================================================================


def test_parse_entity_id_valid():
    domain, obj_id = parse_entity_id("fan.living_room_fan")
    assert domain == "fan"
    assert obj_id == "living_room_fan"


@pytest.mark.parametrize("bad_id", ["", "no-dot", ".", "fan.", ".obj", 123])
def test_parse_entity_id_invalid(bad_id):
    with pytest.raises(ValueError):
        parse_entity_id(bad_id)  # type: ignore[arg-type]


def test_get_domain_from_entity_id_simple():
    assert get_domain_from_entity_id("light.kitchen") == "light"


class TestGetDomainFromEntityId:
    def test_get_domain_from_valid_entity_id(self):
        assert get_domain_from_entity_id("fan.living_room_fan") == "fan"
        assert get_domain_from_entity_id("switch.kitchen_switch") == "switch"
        assert get_domain_from_entity_id("cover.bedroom_blinds") == "cover"

    def test_get_domain_from_invalid_entity_id_raises(self):
        # missing dot
        with pytest.raises(ValueError):
            get_domain_from_entity_id("invalid_entity_id")

        # empty domain
        with pytest.raises(ValueError):
            get_domain_from_entity_id(".no_domain")

        # empty object id
        with pytest.raises(ValueError):
            get_domain_from_entity_id("fan.")


def test_build_service_payload_basic():
    payload = build_service_payload("fan.living_room_fan")
    assert payload == {"entity_id": "fan.living_room_fan"}


def test_build_service_payload_with_extra_and_override():
    payload = build_service_payload(
        "fan.living_room_fan",
        {"percentage": 75, "entity_id": "should_be_ignored"},
    )
    assert payload["entity_id"] == "fan.living_room_fan"
    assert payload["percentage"] == 75
    assert len(payload) == 2


# Extra payload tests (from switch-focused tests)


def test_build_payload_basic_switch():
    payload = build_service_payload("switch.kitchen")
    assert payload == {"entity_id": "switch.kitchen"}


def test_build_payload_extra_switch():
    payload = build_service_payload("switch.kitchen", {"level": 10})
    assert payload == {"entity_id": "switch.kitchen", "level": 10}


def test_build_payload_entity_id_cannot_be_overwritten_switch():
    payload = build_service_payload("switch.kitchen", {"entity_id": "fake", "x": 3})
    assert payload["entity_id"] == "switch.kitchen"
    assert payload["x"] == 3


# =====================================================================
# _normalize_value tests
# =====================================================================


def test_normalize_state_from_bools_and_numbers():
    assert _normalize_value("state", True) is True
    assert _normalize_value("state", False) is False
    assert _normalize_value("state", 1) is True
    assert _normalize_value("state", 0) is False


def test_normalize_state_from_strings():
    assert _normalize_value("state", "ON") is True
    assert _normalize_value("state", "off") is False
    assert _normalize_value("state", "Open") is True
    assert _normalize_value("state", "closed") is False


def test_normalize_state_unknown_returns_original():
    # For values like "stop", handlers can interpret them
    assert _normalize_value("state", "stop") == "stop"


def test_normalize_numeric_points_int_and_float():
    assert _normalize_value("temperature", 23) == 23
    assert _normalize_value("percentage", 12.5) == 12.5


def test_normalize_numeric_points_from_strings():
    assert _normalize_value("temperature", "23") == 23
    assert math.isclose(_normalize_value("percentage", "12.5"), 12.5)
    # Invalid numeric string -> returned as-is; handler can decide how to handle
    assert _normalize_value("temperature", "abc") == "abc"


def test_normalize_other_point_name_returns_original():
    value = {"any": "thing"}
    assert _normalize_value("some_other_point", value) is value


# =====================================================================
# FanDomainHandler tests
# =====================================================================


def test_fan_handler_state_on_off():
    handler = FanHandler("fan.living_room", {})

    call_on = handler.build_service_call("state", "on")
    assert isinstance(call_on, HomeAssistantServiceCall)
    assert call_on.domain == "fan"
    assert call_on.service == "turn_on"
    assert call_on.payload["entity_id"] == "fan.living_room"

    call_off = handler.build_service_call("state", 0)
    assert call_off.domain == "fan"
    assert call_off.service == "turn_off"


def test_fan_handler_state_invalid_raises():
    handler = FanHandler("fan.living_room", {})
    with pytest.raises(ValueError):
        handler.build_service_call("state", "invalid-state")


def test_fan_handler_percentage_valid():
    handler = FanHandler("fan.living_room", {})
    call = handler.build_service_call("percentage", "42")
    assert call.domain == "fan"
    assert call.service == "set_percentage"
    assert call.payload["entity_id"] == "fan.living_room"
    assert call.payload["percentage"] == 42


@pytest.mark.parametrize("bad_value", [-1, 101])
def test_fan_handler_percentage_out_of_range_raises(bad_value):
    handler = FanHandler("fan.living_room", {})
    with pytest.raises(ValueError):
        handler.build_service_call("percentage", bad_value)


class TestFanDomainHandlerState:
    def setup_method(self):
        self.entity_id = "fan.living_room_fan"
        self.handler = FanDomainHandler(self.entity_id, config={})

    @pytest.mark.parametrize("input_value", [True, 1, "1", "on", "ON", "true", "True", "yes"])
    def test_fan_state_on_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "fan"
        assert call.service == "turn_on"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", [False, 0, "0", "off", "OFF", "false", "False", "no"])
    def test_fan_state_off_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "fan"
        assert call.service == "turn_off"
        assert call.payload["entity_id"] == self.entity_id

    def test_fan_state_invalid_value_raises(self):
        with pytest.raises(ValueError):
            self.handler.build_service_call("state", "maybe")


class TestFanDomainHandlerPercentage:
    def setup_method(self):
        self.entity_id = "fan.bedroom_fan"
        self.handler = FanDomainHandler(self.entity_id, config={})

    @pytest.mark.parametrize("point_name", ["percentage", "speed"])
    def test_fan_percentage_valid_range(self, point_name):
        call = self.handler.build_service_call(point_name, 50)
        assert call.domain == "fan"
        assert call.service == "set_percentage"
        assert call.payload["entity_id"] == self.entity_id
        assert call.payload["percentage"] == 50

    @pytest.mark.parametrize("point_name", ["percentage", "speed"])
    def test_fan_percentage_string_value_is_normalized(self, point_name):
        call = self.handler.build_service_call(point_name, "75")
        assert call.domain == "fan"
        assert call.service == "set_percentage"
        assert call.payload["entity_id"] == self.entity_id
        assert call.payload["percentage"] == 75

    @pytest.mark.parametrize("bad_value", [-1, 101, "200"])
    def test_fan_percentage_out_of_range_raises(self, bad_value):
        with pytest.raises(ValueError):
            self.handler.build_service_call("percentage", bad_value)

    def test_fan_unsupported_point_raises_UnsupportedPointError(self):
        with pytest.raises(UnsupportedPointError):
            self.handler.build_service_call("unknown_point", 1)


# =====================================================================
# SwitchDomainHandler tests
# =====================================================================


def test_switch_handler_state_on_off():
    handler = SwitchHandler("switch.kitchen", {})

    call_on = handler.build_service_call("state", True)
    assert call_on.domain == "switch"
    assert call_on.service == "turn_on"
    assert call_on.payload["entity_id"] == "switch.kitchen"

    call_off = handler.build_service_call("state", "off")
    assert call_off.service == "turn_off"


def test_switch_handler_state_invalid_raises():
    handler = SwitchHandler("switch.kitchen", {})
    with pytest.raises(ValueError):
        handler.build_service_call("state", "maybe")


def test_switch_handler_unsupported_point_raises():
    handler = SwitchHandler("switch.kitchen", {})
    with pytest.raises(UnsupportedPointError):
        handler.build_service_call("brightness", 10)


@pytest.fixture
def switch_handler():
    return SwitchDomainHandler("switch.test_switch", config={"dummy": True})


def test_switch_turn_on_bool(switch_handler):
    sc = switch_handler.build_service_call("state", True)
    assert sc.service == "turn_on"
    assert sc.payload["entity_id"] == "switch.test_switch"


def test_switch_turn_off_bool(switch_handler):
    sc = switch_handler.build_service_call("state", False)
    assert sc.service == "turn_off"


def test_switch_turn_on_string(switch_handler):
    sc = switch_handler.build_service_call("state", "on")
    assert sc.service == "turn_on"


def test_switch_turn_off_string(switch_handler):
    sc = switch_handler.build_service_call("state", "off")
    assert sc.service == "turn_off"


def test_switch_turn_on_numeric(switch_handler):
    sc = switch_handler.build_service_call("state", 1)
    assert sc.service == "turn_on"


def test_switch_turn_off_numeric(switch_handler):
    sc = switch_handler.build_service_call("state", 0)
    assert sc.service == "turn_off"


def test_switch_turn_on_case_insensitive(switch_handler):
    sc = switch_handler.build_service_call("state", " On  ")
    assert sc.service == "turn_on"


def test_switch_invalid_string(switch_handler):
    with pytest.raises(ValueError):
        switch_handler.build_service_call("state", "abc")


def test_switch_invalid_none(switch_handler):
    with pytest.raises(ValueError):
        switch_handler.build_service_call("state", None)


def test_switch_invalid_list(switch_handler):
    with pytest.raises(ValueError):
        switch_handler.build_service_call("state", [1, 2, 3])


def test_switch_invalid_dict(switch_handler):
    with pytest.raises(ValueError):
        switch_handler.build_service_call("state", {"x": 1})


def test_switch_unsupported_point(switch_handler):
    with pytest.raises(UnsupportedPointError):
        switch_handler.build_service_call("brightness", 50)


# =====================================================================
# CoverDomainHandler tests
# =====================================================================


class TestCoverDomainHandlerState:
    def setup_method(self):
        self.entity_id = "cover.bedroom_blinds"
        self.handler = CoverDomainHandler(self.entity_id, config={})

    @pytest.mark.parametrize("input_value", ["stop", "STOP", " Stop ", 2, "2"])
    def test_cover_state_stop_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "cover"
        assert call.service == "stop_cover"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", [True, "on", "ON", 1, "1", "true", "yes"])
    def test_cover_state_open_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.service == "open_cover"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", [False, "off", "OFF", 0, "0", "false", "no"])
    def test_cover_state_close_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.service == "close_cover"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", ["open", " OPEN ", "Open"])
    def test_cover_state_explicit_open_string(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.service == "open_cover"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", ["close", " Close ", "CLOSE"])
    def test_cover_state_explicit_close_string(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.service == "close_cover"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("bad", ["maybe", "middle", 999, object()])
    def test_cover_state_invalid_value_raises(self, bad):
        with pytest.raises(ValueError):
            self.handler.build_service_call("state", bad)


class TestCoverDomainHandlerPosition:
    def setup_method(self):
        self.entity_id = "cover.living_room_shades"
        self.handler = CoverDomainHandler(self.entity_id, config={})

    @pytest.mark.parametrize("val", [0, 1, 50, 100, "0", "75", "100"])
    def test_cover_position_valid(self, val):
        call = self.handler.build_service_call("position", val)
        assert call.domain == "cover"
        assert call.service == "set_cover_position"
        assert call.payload["entity_id"] == self.entity_id
        assert 0 <= call.payload["position"] <= 100

    @pytest.mark.parametrize("val", [-1, 101, "150", "-10"])
    def test_cover_position_out_of_range_raises(self, val):
        with pytest.raises(ValueError):
            self.handler.build_service_call("position", val)

    def test_cover_unsupported_point_raises(self):
        with pytest.raises(UnsupportedPointError):
            self.handler.build_service_call("speed", 50)


def test_cover_handler_state_stop_variants():
    handler = CoverHandler("cover.blinds", {})

    call_stop_str = handler.build_service_call("state", "stop")
    assert call_stop_str.domain == "cover"
    assert call_stop_str.service == "stop_cover"

    call_stop_code = handler.build_service_call("state", 2)
    assert call_stop_code.service == "stop_cover"


def test_cover_handler_state_open_close_bool_and_strings():
    handler = CoverHandler("cover.blinds", {})

    call_open = handler.build_service_call("state", "on")
    assert call_open.service == "open_cover"

    call_close = handler.build_service_call("state", "close")
    assert call_close.service == "close_cover"


def test_cover_handler_state_invalid_raises():
    handler = CoverHandler("cover.blinds", {})
    with pytest.raises(ValueError):
        handler.build_service_call("state", "unknown_state")


def test_cover_handler_position_valid():
    handler = CoverHandler("cover.blinds", {})
    call = handler.build_service_call("position", "50")
    assert call.domain == "cover"
    assert call.service == "set_cover_position"
    assert call.payload["position"] == 50


@pytest.mark.parametrize("bad_position", [-10, 200])
def test_cover_handler_position_out_of_range_raises(bad_position):
    handler = CoverHandler("cover.blinds", {})
    with pytest.raises(ValueError):
        handler.build_service_call("position", bad_position)


# =====================================================================
# LightHandler tests
# =====================================================================


def test_light_handler_state_on_off():
    handler = LightHandler("light.kitchen", {})

    call_on = handler.build_service_call("state", "on")
    assert call_on.domain == "light"
    assert call_on.service == "turn_on"

    call_off = handler.build_service_call("state", 0)
    assert call_off.service == "turn_off"


def test_light_handler_state_invalid_raises():
    handler = LightHandler("light.kitchen", {})
    with pytest.raises(ValueError):
        handler.build_service_call("state", "maybe")


def test_light_handler_brightness_valid():
    handler = LightHandler("light.kitchen", {})
    call = handler.build_service_call("brightness", "128")
    assert call.domain == "light"
    assert call.service == "turn_on"
    assert call.payload["brightness"] == 128


@pytest.mark.parametrize("bad_value", [-1, 300])
def test_light_handler_brightness_out_of_range_raises(bad_value):
    handler = LightHandler("light.kitchen", {})
    with pytest.raises(ValueError):
        handler.build_service_call("brightness", bad_value)


# =====================================================================
# ThermostatHandler tests
# =====================================================================


def test_thermostat_handler_state_from_code():
    handler = ThermostatHandler("climate.hvac", {})

    call_off = handler.build_service_call("state", 0)
    assert call_off.domain == "climate"
    assert call_off.service == "set_hvac_mode"
    assert call_off.payload["hvac_mode"] == "off"

    call_heat = handler.build_service_call("state", 2)
    assert call_heat.payload["hvac_mode"] == "heat"


def test_thermostat_handler_state_from_string():
    handler = ThermostatHandler("climate.hvac", {})
    call = handler.build_service_call("state", "cool")
    assert call.payload["hvac_mode"] == "cool"


def test_thermostat_handler_state_invalid_raises():
    handler = ThermostatHandler("climate.hvac", {})
    with pytest.raises(ValueError):
        handler.build_service_call("state", "invalid-mode")


def test_thermostat_handler_temperature_with_celsius_conversion():
    handler = ThermostatHandler("climate.hvac", {"units": "C"})
    call = handler.build_service_call("temperature", 77.0)  # F
    assert call.domain == "climate"
    assert call.service == "set_temperature"
    assert pytest.approx(call.payload["temperature"], rel=1e-3) == 25.0


def test_thermostat_handler_temperature_numeric_validation():
    handler = ThermostatHandler("climate.hvac", {})
    call = handler.build_service_call("temperature", "21.5")
    assert pytest.approx(call.payload["temperature"], rel=1e-3) == 21.5


def test_thermostat_handler_temperature_invalid_raises():
    handler = ThermostatHandler("climate.hvac", {})
    with pytest.raises(ValueError):
        handler.build_service_call("temperature", "not-a-number")


# =====================================================================
# _send_service_call tests
# =====================================================================


class DummyResponse:
    def __init__(self, status_code=200, text="OK"):
        self.status_code = status_code
        self.text = text


class TestSendServiceCall:
    def test_send_service_call_success(self, monkeypatch):
        calls = {}

        def fake_post(url, headers=None, json=None):
            calls["url"] = url
            calls["headers"] = headers
            calls["json"] = json
            return DummyResponse(status_code=200, text="OK")

        monkeypatch.setattr(requests, "post", fake_post)

        config = {
            "base_url": "http://homeassistant.local:8123",
            "access_token": "TEST_TOKEN",
        }
        payload = {"entity_id": "fan.living_room_fan"}

        _send_service_call(config, "fan", "turn_on", payload)

        assert calls["url"] == "http://homeassistant.local:8123/api/services/fan/turn_on"
        assert calls["headers"]["Authorization"] == "Bearer TEST_TOKEN"
        assert calls["headers"]["Content-Type"] == "application/json"
        assert calls["json"] == payload

    def test_send_service_call_missing_base_url_raises(self):
        config = {
            # "base_url" missing
            "access_token": "TEST_TOKEN",
        }
        payload = {"entity_id": "fan.living_room_fan"}

        with pytest.raises(ServiceCallError) as excinfo:
            _send_service_call(config, "fan", "turn_on", payload)

        assert "base_url is not configured" in str(excinfo.value)

    def test_send_service_call_missing_access_token_raises(self):
        config = {
            "base_url": "http://homeassistant.local:8123",
            # "access_token" missing
        }
        payload = {"entity_id": "fan.living_room_fan"}

        with pytest.raises(ServiceCallError) as excinfo:
            _send_service_call(config, "fan", "turn_on", payload)

        assert "access_token is not configured" in str(excinfo.value)

    def test_send_service_call_http_error_status_raises(self, monkeypatch):
        def fake_post(url, headers=None, json=None):
            return DummyResponse(status_code=500, text="Internal Server Error")

        monkeypatch.setattr(requests, "post", fake_post)

        config = {
            "base_url": "http://homeassistant.local:8123",
            "access_token": "TEST_TOKEN",
        }
        payload = {"entity_id": "fan.living_room_fan"}

        with pytest.raises(ServiceCallError) as excinfo:
            _send_service_call(config, "fan", "turn_on", payload)

        assert "Status code: 500" in str(excinfo.value)

    def test_send_service_call_request_exception_raises(self, monkeypatch):
        def fake_post(url, headers=None, json=None):
            raise requests.RequestException("network down")

        monkeypatch.setattr(requests, "post", fake_post)

        config = {
            "base_url": "http://homeassistant.local:8123",
            "access_token": "TEST_TOKEN",
        }
        payload = {"entity_id": "fan.living_room_fan"}

        with pytest.raises(ServiceCallError) as excinfo:
            _send_service_call(config, "fan", "turn_on", payload)

        assert "network down" in str(excinfo.value)
