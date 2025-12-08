# ======================================================================
# Standalone unit tests for SwitchDomainHandler
# These tests do NOT require Volttron, Home Assistant, or any network.
# They only validate handler logic, as required by the latest professor
# clarification.
# ======================================================================

import pytest
import os
import sys

# Add project root to Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from platform_driver.interfaces.home_assistant import (
    SwitchDomainHandler,
    UnsupportedPointError,
    _normalize_value,
    build_service_payload,
)


# ----------------------------------------------------------------------
# Fixture: simple handler instance
# ----------------------------------------------------------------------
@pytest.fixture
def handler():
    return SwitchDomainHandler("switch.test_switch", config={})


# ----------------------------------------------------------------------
# Normal Path Tests
# ----------------------------------------------------------------------

def test_switch_turn_on_bool(handler):
    sc = handler.build_service_call("state", True)
    assert sc.domain == "switch"
    assert sc.service == "turn_on"
    assert sc.payload == {"entity_id": "switch.test_switch"}


def test_switch_turn_off_bool(handler):
    sc = handler.build_service_call("state", False)
    assert sc.service == "turn_off"


def test_switch_turn_on_string(handler):
    sc = handler.build_service_call("state", "on")
    assert sc.service == "turn_on"


def test_switch_turn_off_string(handler):
    sc = handler.build_service_call("state", "off")
    assert sc.service == "turn_off"


def test_switch_turn_on_numeric(handler):
    sc = handler.build_service_call("state", 1)
    assert sc.service == "turn_on"


def test_switch_turn_off_numeric(handler):
    sc = handler.build_service_call("state", 0)
    assert sc.service == "turn_off"


# ----------------------------------------------------------------------
# Error path: invalid → handler must reject (not normalize)
# ----------------------------------------------------------------------

def test_switch_invalid_state_value(handler):
    # _normalize_value returns the original string "abc"
    # So build_service_call should raise ValueError, not normalize
    with pytest.raises(ValueError):
        handler.build_service_call("state", "abc")


# ----------------------------------------------------------------------
# Unsupported point names
# ----------------------------------------------------------------------

def test_switch_unsupported_point(handler):
    with pytest.raises(UnsupportedPointError):
        handler.build_service_call("brightness", 50)


# ----------------------------------------------------------------------
# _normalize_value tests (updated to match new behavior!)
# ----------------------------------------------------------------------

def test_normalize_state_true_cases():
    assert _normalize_value("state", "on") is True
    assert _normalize_value("state", "True") is True
    assert _normalize_value("state", True) is True
    assert _normalize_value("state", 1) is True


def test_normalize_state_false_cases():
    assert _normalize_value("state", "off") is False
    assert _normalize_value("state", "False") is False
    assert _normalize_value("state", False) is False
    assert _normalize_value("state", 0) is False


def test_normalize_state_unrecognized_returns_original():
    # NEW RULE: unrecognized state returns raw value
    assert _normalize_value("state", "abc") == "abc"


# ----------------------------------------------------------------------
# build_service_payload tests
# ----------------------------------------------------------------------

def test_build_service_payload_basic():
    payload = build_service_payload("switch.kitchen")
    assert payload == {"entity_id": "switch.kitchen"}


def test_build_service_payload_extra():
    payload = build_service_payload("switch.kitchen", {"level": 10})
    assert payload == {"entity_id": "switch.kitchen", "level": 10}

    payload2 = build_service_payload("switch.kitchen", {"entity_id": "fake", "level": 20})
    assert payload2["entity_id"] == "switch.kitchen"
    assert payload2["level"] == 20