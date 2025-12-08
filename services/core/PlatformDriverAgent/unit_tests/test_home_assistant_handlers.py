import pytest

from platform_driver.interfaces.home_assistant import (
    parse_entity_id,
    ensure_supported_domain,
    build_service_url,
    build_service_payload,
    FanHandler,
    SwitchHandler,
    CoverHandler,
    UnsupportedDomainError,
    UnsupportedPointError,
    HomeAssistantServiceCall,
)


# =====================================================================
# parse_entity_id tests
# =====================================================================


class TestParseEntityId:
    def test_parse_valid_entity_ids(self):
        domain, obj = parse_entity_id("fan.living_room_fan")
        assert domain == "fan"
        assert obj == "living_room_fan"

        domain, obj = parse_entity_id("switch.kitchen_switch")
        assert domain == "switch"
        assert obj == "kitchen_switch"

        domain, obj = parse_entity_id("cover.bedroom_blinds")
        assert domain == "cover"
        assert obj == "bedroom_blinds"

    @pytest.mark.parametrize(
        "bad_entity_id",
        [
            "invalid_without_dot",
            ".no_domain",
            "fan.",
            "",
        ],
    )
    def test_parse_invalid_entity_ids_raise_value_error(self, bad_entity_id):
        with pytest.raises(ValueError):
            parse_entity_id(bad_entity_id)


# =====================================================================
# ensure_supported_domain tests
# =====================================================================


class TestEnsureSupportedDomain:
    def test_supported_domain_returns_domain(self):
        supported = ["fan", "switch", "cover"]
        domain = ensure_supported_domain("fan.living_room_fan", supported)
        assert domain == "fan"

        domain = ensure_supported_domain("switch.kitchen", supported)
        assert domain == "switch"

    @pytest.mark.parametrize(
        "entity_id",
        [
            "light.living_room",
            "climate.thermostat",
            "media_player.tv",
        ],
    )
    def test_unsupported_domain_raises_custom_error(self, entity_id):
        supported = ["fan", "switch", "cover"]
        with pytest.raises(UnsupportedDomainError) as excinfo:
            ensure_supported_domain(entity_id, supported)

        # 错误信息里应该包含 entity_id，方便排查
        msg = str(excinfo.value)
        assert entity_id in msg


# =====================================================================
# build_service_url tests
# =====================================================================


class TestBuildServiceURL:
    def test_build_service_url_without_trailing_slash(self):
        base_url = "http://homeassistant.local:8123"
        url = build_service_url(base_url, "fan", "turn_on")
        assert url == "http://homeassistant.local:8123/api/services/fan/turn_on"

    def test_build_service_url_with_trailing_slash(self):
        base_url = "http://homeassistant.local:8123/"
        url = build_service_url(base_url, "switch", "turn_off")
        assert url == "http://homeassistant.local:8123/api/services/switch/turn_off"


# =====================================================================
# build_service_payload tests
# =====================================================================


class TestBuildServicePayload:
    def test_payload_with_only_entity_id(self):
        payload = build_service_payload("fan.living_room_fan", None)
        assert payload == {"entity_id": "fan.living_room_fan"}

    def test_payload_merges_extra_fields(self):
        payload = build_service_payload(
            "fan.living_room_fan",
            {"percentage": 75, "custom": "value"},
        )
        assert payload["entity_id"] == "fan.living_room_fan"
        assert payload["percentage"] == 75
        assert payload["custom"] == "value"

    def test_payload_does_not_allow_overriding_entity_id(self):
        payload = build_service_payload(
            "fan.living_room_fan",
            {"entity_id": "fan.other", "percentage": 50},
        )
        # entity_id 应该始终是第一个参数，而不是 extra 里的值
        assert payload["entity_id"] == "fan.living_room_fan"
        assert payload["percentage"] == 50


# =====================================================================
# FanHandler tests
# =====================================================================


class TestFanHandlerState:
    def setup_method(self):
        self.entity_id = "fan.living_room_fan"
        self.handler = FanHandler(self.entity_id, config={})

    @pytest.mark.parametrize("input_value", [True, 1, "1", "on"])
    def test_fan_state_on_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert isinstance(call, HomeAssistantServiceCall)
        assert call.domain == "fan"
        assert call.service == "turn_on"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", [False, 0, "0", "off"])
    def test_fan_state_off_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "fan"
        assert call.service == "turn_off"
        assert call.payload["entity_id"] == self.entity_id

    def test_fan_state_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError):
            self.handler.build_service_call("state", "maybe")


class TestFanHandlerPercentage:
    def setup_method(self):
        self.entity_id = "fan.bedroom_fan"
        self.handler = FanHandler(self.entity_id, config={})

    def test_fan_percentage_valid_int(self):
        call = self.handler.build_service_call("percentage", 50)
        assert call.domain == "fan"
        assert call.service == "set_percentage"
        assert call.payload["entity_id"] == self.entity_id
        assert call.payload["percentage"] == 50

    def test_fan_percentage_valid_string(self):
        call = self.handler.build_service_call("percentage", "75")
        assert call.payload["percentage"] == 75

    @pytest.mark.parametrize("bad_value", [-1, 101, "200"])
    def test_fan_percentage_out_of_range_or_invalid_raises(self, bad_value):
        with pytest.raises(ValueError):
            self.handler.build_service_call("percentage", bad_value)

    def test_fan_unsupported_point_raises_UnsupportedPointError(self):
        with pytest.raises(UnsupportedPointError):
            self.handler.build_service_call("unknown_point", 1)


# =====================================================================
# SwitchHandler tests
# =====================================================================


class TestSwitchHandlerState:
    def setup_method(self):
        self.entity_id = "switch.kitchen_switch"
        self.handler = SwitchHandler(self.entity_id, config={})

    @pytest.mark.parametrize("input_value", [True, 1, "1", "on"])
    def test_switch_state_on_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "switch"
        assert call.service == "turn_on"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("input_value", [False, 0, "0", "off"])
    def test_switch_state_off_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "switch"
        assert call.service == "turn_off"
        assert call.payload["entity_id"] == self.entity_id

    def test_switch_state_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError):
            self.handler.build_service_call("state", "maybe")

    def test_switch_unsupported_point_raises_UnsupportedPointError(self):
        with pytest.raises(UnsupportedPointError):
            self.handler.build_service_call("unknown_point", 1)


# =====================================================================
# CoverHandler tests
# =====================================================================


class TestCoverHandlerState:
    def setup_method(self):
        self.entity_id = "cover.bedroom_blinds"
        self.handler = CoverHandler(self.entity_id, config={})

    @pytest.mark.parametrize("value", ["open", 0, "0"])
    def test_cover_state_open_variants(self, value):
        call = self.handler.build_service_call("state", value)
        assert call.domain == "cover"
        assert call.service == "open_cover"
        assert call.payload["entity_id"] == self.entity_id

    @pytest.mark.parametrize("value", ["close", 1, "1"])
    def test_cover_state_close_variants(self, value):
        call = self.handler.build_service_call("state", value)
        assert call.service == "close_cover"

    @pytest.mark.parametrize("value", ["stop", 2, "2"])
    def test_cover_state_stop_variants(self, value):
        call = self.handler.build_service_call("state", value)
        assert call.service == "stop_cover"

    def test_cover_state_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError):
            self.handler.build_service_call("state", "half_open")


class TestCoverHandlerPosition:
    def setup_method(self):
        self.entity_id = "cover.living_room_cover"
        self.handler = CoverHandler(self.entity_id, config={})

    @pytest.mark.parametrize("position", [0, 50, 100])
    def test_cover_position_valid_range(self, position):
        call = self.handler.build_service_call("position", position)
        assert call.domain == "cover"
        assert call.service == "set_cover_position"
        assert call.payload["entity_id"] == self.entity_id
        assert call.payload["position"] == position

    def test_cover_position_string_is_converted_to_int(self):
        call = self.handler.build_service_call("position", "75")
        assert call.payload["position"] == 75

    @pytest.mark.parametrize("bad_value", [-1, 101, "200"])
    def test_cover_position_out_of_range_or_invalid_raises(self, bad_value):
        with pytest.raises(ValueError):
            self.handler.build_service_call("position", bad_value)

    def test_cover_unsupported_point_raises_UnsupportedPointError(self):
        with pytest.raises(UnsupportedPointError):
            self.handler.build_service_call("unknown_point", 0)

import pytest

from platform_driver.interfaces.home_assistant import (
    get_domain_from_entity_id,
    _normalize_value,
)


# =====================================================================
# get_domain_from_entity_id tests
# =====================================================================


class TestGetDomainFromEntityId:
    def test_valid_entity_ids(self):
        assert get_domain_from_entity_id("fan.living_room_fan") == "fan"
        assert get_domain_from_entity_id("switch.kitchen_switch") == "switch"
        assert get_domain_from_entity_id("cover.bedroom_blinds") == "cover"
        assert get_domain_from_entity_id("light.living_room") == "light"
        assert get_domain_from_entity_id("climate.thermostat") == "climate"

    @pytest.mark.parametrize(
        "bad_value",
        [
            "no_dot",
            ".no_domain",
            "fan.",
            "",
            "   ",
            123,          # 非 str
            None,
        ],
    )
    def test_invalid_entity_ids_raise_value_error(self, bad_value):
        with pytest.raises(ValueError):
            get_domain_from_entity_id(bad_value)  # type: ignore[arg-type]


# =====================================================================
# _normalize_value tests
# =====================================================================


class TestNormalizeValueState:
    def test_state_bool_passthrough(self):
        assert _normalize_value("state", True) is True
        assert _normalize_value("state", False) is False

    @pytest.mark.parametrize("val", ["on", "ON", " true ", "open", "OPEN"])
    def test_state_truthy_strings(self, val):
        assert _normalize_value("state", val) is True

    @pytest.mark.parametrize("val", ["off", "OFF", " false ", "closed", "CLOSE"])
    def test_state_falsy_strings(self, val):
        assert _normalize_value("state", val) is False

    @pytest.mark.parametrize("val", [1, 1.0])
    def test_state_numeric_one_is_true(self, val):
        assert _normalize_value("state", val) is True

    @pytest.mark.parametrize("val", [0, 0.0])
    def test_state_numeric_zero_is_false(self, val):
        assert _normalize_value("state", val) is False

    def test_state_other_values_return_original(self):
        # 比如 cover 的 "stop" 就会走到这里，保持原样
        assert _normalize_value("state", "stop") == "stop"
        obj = object()
        assert _normalize_value("state", obj) is obj


class TestNormalizeValueNumeric:
    @pytest.mark.parametrize(
        "point_name",
        ["temperature", "heat_setpoint", "cool_setpoint", "position", "percentage", "brightness", "speed"],
    )
    def test_numeric_int_and_float_passthrough(self, point_name):
        assert _normalize_value(point_name, 10) == 10
        assert _normalize_value(point_name, 12.5) == 12.5

    @pytest.mark.parametrize(
        "point_name",
        ["temperature", "heat_setpoint", "cool_setpoint", "position", "percentage", "brightness", "speed"],
    )
    def test_numeric_strings_are_parsed(self, point_name):
        assert _normalize_value(point_name, "21.5") == 21.5
        assert _normalize_value(point_name, " 50 ") == 50

    @pytest.mark.parametrize(
        "point_name",
        ["temperature", "heat_setpoint", "cool_setpoint", "position", "percentage", "brightness", "speed"],
    )
    def test_numeric_invalid_string_returns_original(self, point_name):
        # 规范里我们约定：解析失败就原样返回，不在这里抛异常
        val = "abc"
        result = _normalize_value(point_name, val)
        assert result == val

    @pytest.mark.parametrize(
        "point_name",
        ["temperature", "heat_setpoint", "cool_setpoint", "position", "percentage", "brightness", "speed"],
    )
    def test_numeric_other_types_return_original(self, point_name):
        obj = object()
        assert _normalize_value(point_name, obj) is obj


class TestNormalizeValueOtherPoints:
    def test_non_special_point_returns_original(self):
        assert _normalize_value("some_custom_point", "raw") == "raw"
        assert _normalize_value("some_custom_point", 123) == 123
