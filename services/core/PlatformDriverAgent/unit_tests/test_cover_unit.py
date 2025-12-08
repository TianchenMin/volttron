import pytest
from unittest.mock import MagicMock

from platform_driver.interfaces.home_assistant import (
    CoverDomainHandler,
    HomeAssistantServiceCall,
    UnsupportedPointError
)

class TestCoverDomainHandler:
    """
    Unit tests for CoverDomainHandler.
    Responsible: @yany
    """

    @pytest.fixture
    def handler(self):
        return CoverDomainHandler(entity_id="cover.garage_door", config={})

    def test_state_open(self, handler):
        """Test: writing 'state' = True/On/Open -> open_cover service."""
        # Case 1: Boolean True
        call = handler.build_service_call("state", True)
        assert isinstance(call, HomeAssistantServiceCall)
        assert call.domain == "cover"
        assert call.service == "open_cover"
        assert call.payload["entity_id"] == "cover.garage_door"

        # Case 2: String "open" (normalization check)
        call_str = handler.build_service_call("state", "open")
        assert call_str.service == "open_cover"

    def test_state_close(self, handler):
        """Test: writing 'state' = False/Off/Close -> close_cover service."""
        # Case 1: Boolean False
        call = handler.build_service_call("state", False)
        assert call.domain == "cover"
        assert call.service == "close_cover"
        
        # Case 2: String "OFF"
        call_str = handler.build_service_call("state", "OFF")
        assert call_str.service == "close_cover"

    def test_state_stop(self, handler):
        """Test: writing 'state' = 'stop' -> stop_cover service."""
        call = handler.build_service_call("state", "stop")
        assert call.domain == "cover"
        assert call.service == "stop_cover"
        assert call.payload["entity_id"] == "cover.garage_door"

    def test_position_valid(self, handler):
        """Test: writing 'position' with valid numbers."""
        # Case 1: Integer
        call = handler.build_service_call("position", 50)
        assert call.domain == "cover"
        assert call.service == "set_cover_position"
        assert call.payload["position"] == 50

        # Case 2: String parsing
        call_str = handler.build_service_call("position", "100")
        assert call_str.payload["position"] == 100

    def test_position_invalid_range(self, handler):
        """Test: writing 'position' outside 0-100 raises ValueError."""
        with pytest.raises(ValueError) as excinfo:
            handler.build_service_call("position", -1)
        assert "within [0, 100]" in str(excinfo.value)

        with pytest.raises(ValueError) as excinfo:
            handler.build_service_call("position", 101)
        assert "within [0, 100]" in str(excinfo.value)

    def test_unsupported_point(self, handler):
        """Test: writing to a point that Cover doesn't support (e.g., 'speed')."""
        with pytest.raises(UnsupportedPointError) as excinfo:
            handler.build_service_call("speed", 50)
        assert "Unsupported point 'speed'" in str(excinfo.value)

    def test_invalid_state_value(self, handler):
        """Test: writing garbage to 'state' raises ValueError."""
        with pytest.raises(ValueError) as excinfo:
            handler.build_service_call("state", "simulate_random_junk")
        assert "Invalid value" in str(excinfo.value)