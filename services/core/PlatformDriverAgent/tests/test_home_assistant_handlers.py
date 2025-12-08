import math
import pytest

from platform_driver.interfaces.home_assistant import (
    parse_entity_id,
    get_domain_from_entity_id,
    build_service_payload,
    _normalize_value,
    FanHandler,
    SwitchHandler,
    CoverHandler,
    LightHandler,
    ThermostatHandler,
    HomeAssistantServiceCall,
    UnsupportedPointError,
)


# =====================================================================
# Utility functions tests
# =====================================================================


def test_parse_entity_id_valid():
    domain, obj_id = parse_entity_id("fan.living_room_fan")
    assert domain == "fan"
    assert obj_id == "living_room_fan"


@pytest.mark.parametrize("bad_id", ["", "no-dot", ".", "fan.", ".obj", 123])
def test_parse_entity_id_invalid(bad_id):
    with pytest.raises(ValueError):
        parse_entity_id(bad_id)  # type: ignore[arg-type]


def test_get_domain_from_entity_id():
    assert get_domain_from_entity_id("light.kitchen") == "light"


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
    # Invalid numeric string -> returned as-is
    assert _normalize_value("temperature", "abc") == "abc"


def test_normalize_other_point_name_returns_original():
    value = {"any": "thing"}
    assert _normalize_value("some_other_point", value) is value


# =====================================================================
# FanHandler tests
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


# =====================================================================
# SwitchHandler tests
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


# =====================================================================
# CoverHandler tests
# =====================================================================


def test_cover_handler_state_stop_variants():
    handler = CoverHandler("cover.blinds", {})

    call_stop_str = handler.build_service_call("state", "stop")
    assert call_stop_str.domain == "cover"
    assert call_stop_str.service == "stop_cover"

    call_stop_code = handler.build_service_call("state", 2)
    assert call_stop_code.service == "stop_cover"


def test_cover_handler_state_open_close_bool_and_strings():
    handler = CoverHandler("cover.blinds", {})

    # bool-like True -> open_cover
    call_open = handler.build_service_call("state", "on")
    assert call_open.service == "open_cover"

    # explicit "close"
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
    assert call.service == "turn_on"  # brightness uses turn_on
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
    # Units "C" in config -> treat input as Fahrenheit and convert to Celsius
    handler = ThermostatHandler("climate.hvac", {"units": "C"})
    call = handler.build_service_call("temperature", 77.0)  # F
    assert call.domain == "climate"
    assert call.service == "set_temperature"
    # 77 F ~ 25 C
    assert pytest.approx(call.payload["temperature"], rel=1e-3) == 25.0


def test_thermostat_handler_temperature_numeric_validation():
    handler = ThermostatHandler("climate.hvac", {})
    call = handler.build_service_call("temperature", "21.5")
    assert pytest.approx(call.payload["temperature"], rel=1e-3) == 21.5


def test_thermostat_handler_temperature_invalid_raises():
    handler = ThermostatHandler("climate.hvac", {})
    with pytest.raises(ValueError):
        handler.build_service_call("temperature", "not-a-number")
