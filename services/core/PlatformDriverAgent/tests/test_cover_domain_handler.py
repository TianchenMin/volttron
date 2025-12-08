import pytest
from platform_driver.interfaces.home_assistant import (
    CoverDomainHandler,
    UnsupportedPointError,
    HomeAssistantServiceCall,
)


# =====================================================================
# CoverDomainHandler: state tests
# =====================================================================

class TestCoverDomainHandlerState:
    def setup_method(self):
        self.entity_id = "cover.bedroom_blinds"
        self.handler = CoverDomainHandler(self.entity_id, config={})

    # ------------------------------------------------------------
    # STOP logic
    # ------------------------------------------------------------
    @pytest.mark.parametrize("input_value", ["stop", "STOP", " Stop ", 2, "2"])
    def test_cover_state_stop_variants(self, input_value):
        call = self.handler.build_service_call("state", input_value)
        assert call.domain == "cover"
        assert call.service == "stop_cover"
        assert call.payload["entity_id"] == self.entity_id

    # ------------------------------------------------------------
    # Boolean-like → open_cover / close_cover
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Fallback: open / close strings (not boolean)
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Invalid state should raise
    # ------------------------------------------------------------
    @pytest.mark.parametrize("bad", ["maybe", "middle", 999, object()])
    def test_cover_state_invalid_value_raises(self, bad):
        with pytest.raises(ValueError):
            self.handler.build_service_call("state", bad)


# =====================================================================
# CoverDomainHandler: position tests
# =====================================================================

class TestCoverDomainHandlerPosition:
    def setup_method(self):
        self.entity_id = "cover.living_room_shades"
        self.handler = CoverDomainHandler(self.entity_id, config={})

    # ------------------------------------------------------------
    # Valid position
    # ------------------------------------------------------------
    @pytest.mark.parametrize("val", [0, 1, 50, 100, "0", "75", "100"])
    def test_cover_position_valid(self, val):
        call = self.handler.build_service_call("position", val)
        assert call.domain == "cover"
        assert call.service == "set_cover_position"
        assert call.payload["entity_id"] == self.entity_id
        assert 0 <= call.payload["position"] <= 100

    # ------------------------------------------------------------
    # Out-of-range values
    # ------------------------------------------------------------
    @pytest.mark.parametrize("val", [-1, 101, "150", "-10"])
    def test_cover_position_out_of_range_raises(self, val):
        with pytest.raises(ValueError):
            self.handler.build_service_call("position", val)

    # ------------------------------------------------------------
    # Unsupported point
    # ------------------------------------------------------------
    def test_cover_unsupported_point_raises(self):
        with pytest.raises(UnsupportedPointError):
            self.handler.build_service_call("speed", 50)
