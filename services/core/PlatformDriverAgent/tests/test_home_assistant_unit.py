import pytest
import requests

from platform_driver.interfaces.home_assistant import (
    get_domain_from_entity_id,
    _normalize_value,
    FanDomainHandler,
    UnsupportedPointError,
    _send_service_call,
    ServiceCallError,
)



# =====================================================================
# get_domain_from_entity_id tests
# =====================================================================


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


# =====================================================================
# _normalize_value tests
# =====================================================================


class TestNormalizeValueState:
    def test_state_true_variants(self):
        true_values = ("on", "ON", "true", "True", "yes", "YES", 1, "1", True)
        for v in true_values:
            assert _normalize_value("state", v) is True

    def test_state_false_variants(self):
        false_values = ("off", "OFF", "false", "False", "no", "NO", 0, "0", False)
        for v in false_values:
            assert _normalize_value("state", v) is False

    def test_state_invalid_value_raises(self):
        with pytest.raises(ValueError):
            _normalize_value("state", "maybe")


class TestNormalizeValueNumeric:
    @pytest.mark.parametrize("point_name", ["temperature", "position", "percentage", "speed"])
    def test_numeric_accepts_int_and_float(self, point_name):
        assert _normalize_value(point_name, 10) == 10
        assert _normalize_value(point_name, 12.5) == 12.5

    @pytest.mark.parametrize("point_name", ["temperature", "position", "percentage", "speed"])
    def test_numeric_accepts_string(self, point_name):
        assert _normalize_value(point_name, "21.5") == 21.5
        assert _normalize_value(point_name, "50") == 50.0

    @pytest.mark.parametrize("point_name", ["temperature", "position", "percentage", "speed"])
    def test_numeric_invalid_string_raises(self, point_name):
        with pytest.raises(ValueError):
            _normalize_value(point_name, "abc")

    @pytest.mark.parametrize("point_name", ["temperature", "position", "percentage", "speed"])
    def test_numeric_unsupported_type_raises(self, point_name):
        with pytest.raises(ValueError):
            _normalize_value(point_name, object())


# =====================================================================
# FanDomainHandler tests
# =====================================================================


class TestFanDomainHandlerState:
    def setup_method(self):
        # 每个用例用一个简单的 handler，config 传空 dict 即可
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
            # 记录调用参数，方便下面断言
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

        # 不应该抛异常
        _send_service_call(config, "fan", "turn_on", payload)

        assert calls["url"] == "http://homeassistant.local:8123/api/services/fan/turn_on"
        assert calls["headers"]["Authorization"] == "Bearer TEST_TOKEN"
        assert calls["headers"]["Content-Type"] == "application/json"
        assert calls["json"] == payload

    def test_send_service_call_missing_base_url_raises(self):
        config = {
            # "base_url" 缺失
            "access_token": "TEST_TOKEN",
        }
        payload = {"entity_id": "fan.living_room_fan"}

        with pytest.raises(ServiceCallError) as excinfo:
            _send_service_call(config, "fan", "turn_on", payload)

        assert "base_url is not configured" in str(excinfo.value)

    def test_send_service_call_missing_access_token_raises(self):
        config = {
            "base_url": "http://homeassistant.local:8123",
            # access_token 缺失
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

        # 简单检查一下错误信息里包含 status code
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
