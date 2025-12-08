import pytest
from unittest.mock import patch, MagicMock
from typing import Any, Dict, Optional

from platform_driver.interfaces.home_assistant import (
    CoverDomainHandler,
    HomeAssistantServiceCall,
    UnsupportedPointError,
    build_service_payload,
    _normalize_value,
    HomeAssistantDomainHandler, 
)


# --- Mocking Utilities ---

# 1. Mock implementation for build_service_payload
def mock_build_payload(entity_id: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Simulates the build_service_payload utility function."""
    payload: Dict[str, Any] = {"entity_id": entity_id}
    if extra:
        payload.update(extra)
    return payload


# 2. Patch Target Path: Use the absolute path where the function is imported/used.
PATCH_PATH = 'platform_driver.interfaces.home_assistant'


@pytest.fixture
def handler() -> CoverDomainHandler:
    """Fixture to create a CoverDomainHandler instance for every test."""
    ENTITY_ID = "cover.unit_test_blinds"
    return CoverDomainHandler(ENTITY_ID, {})


@patch(f'{PATCH_PATH}.build_service_payload', side_effect=mock_build_payload)
class TestCoverDomainHandler:
    ENTITY_ID = "cover.unit_test_blinds"
    
    # -----------------------------------------------------
    # Test "state" point: open/close/stop
    # -----------------------------------------------------

    @pytest.mark.parametrize("value", ["stop", " SToP ", 2, "2"])
    @patch(f'{PATCH_PATH}._normalize_value')
    def test_state_stop_variants(self, mock_normalize: MagicMock, mock_payload: MagicMock, handler: CoverDomainHandler, value: Any):
        """Tests that explicit 'stop' (string/code 2) is handled before normalization."""
        call = handler.build_service_call("state", value)
        
        # Verification: Stop check must be completed before calling _normalize_value
        mock_normalize.assert_not_called()
        
        assert call.domain == "cover"
        assert call.service == "stop_cover"


    @patch(f'{PATCH_PATH}._normalize_value')
    @pytest.mark.parametrize("normalized_return, expected_service", [
        (True, "open_cover"),
        (False, "close_cover"),
    ])
    def test_state_open_close_via_normalize(self, mock_normalize: MagicMock, mock_payload: MagicMock, handler: CoverDomainHandler, normalized_return: bool, expected_service: str):
        """Tests bool-like values handled by normalization map to open/close services."""
        input_value = "raw_input" 
        mock_normalize.return_value = normalized_return
        
        call = handler.build_service_call("state", input_value)
        
        mock_normalize.assert_called_once_with("state", input_value)
        assert call.service == expected_service


    @patch(f'{PATCH_PATH}._normalize_value')
    @pytest.mark.parametrize("invalid_input", [3, {"a": 1}])
    def test_state_invalid_raises_value_error(self, mock_normalize: MagicMock, mock_payload: MagicMock, handler: CoverDomainHandler, invalid_input: Any):
        """
        Tests that invalid state values result in a ValueError (due to strict _normalize_value).
        """
        # Setup: Mock _normalize_value to raise ValueError as per its strict spec
        mock_normalize.side_effect = ValueError("Cannot normalize state value")
        
        # Handler should catch the exception and re-raise with better context
        with pytest.raises(ValueError, match="Invalid state value for cover"):
            handler.build_service_call("state", invalid_input)


    # -----------------------------------------------------
    # Test "position" point: set_cover_position
    # -----------------------------------------------------

    @patch(f'{PATCH_PATH}._normalize_value')
    @pytest.mark.parametrize("normalized_input, expected_pos", [
        (0, 0), (100, 100),
        (50.5, 50), # Test float truncation
        ("75", 75), # Test string input handled by normalizer
    ])
    def test_position_valid(self, mock_normalize: MagicMock, mock_payload: MagicMock, handler: CoverDomainHandler, normalized_input: Any, expected_pos: int):
        """Tests valid position inputs result in correct set_cover_position call."""
        input_value = "raw_value_ignored"
        mock_normalize.return_value = normalized_input
        
        call = handler.build_service_call("position", input_value)
        
        mock_payload.assert_called_once_with(handler.entity_id, {"position": expected_pos})
        assert call.service == "set_cover_position"


    @patch(f'{PATCH_PATH}._normalize_value')
    @pytest.mark.parametrize("bad_input", [-1, 101])
    def test_position_out_of_range_raises(self, mock_normalize: MagicMock, mock_payload: MagicMock, handler: CoverDomainHandler, bad_input: int):
        """Tests position values outside of [0, 100] raise ValueError (Handler check)."""
        mock_normalize.return_value = bad_input
        # The range check is performed by the Handler itself after int() conversion
        with pytest.raises(ValueError, match=r"Cover position must be between 0 and 100"):
            handler.build_service_call("position", "irrelevant_input")

    @patch(f'{PATCH_PATH}._normalize_value')
    @pytest.mark.parametrize("invalid_input", ["invalid_string", None])
    def test_position_non_numeric_raises(self, mock_normalize: MagicMock, mock_payload: MagicMock, handler: CoverDomainHandler, invalid_input: Any):
        """Tests non-numeric input for position raises ValueError (caught from _normalize_value)."""
        # Setup: Mock _normalize_value to raise ValueError (simulating non-numeric failure)
        mock_normalize.side_effect = ValueError("Cannot normalize numeric value")
        
        # Handler should catch the exception and re-raise with better context
        with pytest.raises(ValueError, match="Cover position processing failed for"):
            handler.build_service_call("position", "some_non_numeric_value")


    # -----------------------------------------------------
    # General Test
    # -----------------------------------------------------

    def test_unsupported_point_raises(self, handler: CoverDomainHandler):
        """Tests requesting an unsupported point raises UnsupportedPointError."""
        with pytest.raises(UnsupportedPointError) as excinfo:
            handler.build_service_call("brightness", 50)
            
        assert "brightness" in str(excinfo.value)
