# ======================================================================
# Standalone unit tests for SwitchDomainHandler
# These tests do NOT require Volttron, Home Assistant, or any network.
# They validate handler logic only.
# ======================================================================

import pytest
import os
import sys

# Add project root for direct import
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from platform_driver.interfaces.home_assistant import (
    SwitchDomainHandler,
    UnsupportedPointError,
    _normalize_value,
    build_service_payload,
)


# ----------------------------------------------------------------------
# Fixture: basic handler instance
# ----------------------------------------------------------------------
@pytest.fixture
def handler():
    return SwitchDomainHandler("switch.test_switch", config={"dummy": True})


# ----------------------------------------------------------------------
# Normal Path Tests (turn_on / turn_off)
# ----------------------------------------------------------------------

def test_switch_turn_on_bool(handler):
    sc = handler.build_service_call("state", True)
    assert sc.service == "turn_on"
    assert sc.payload["entity_id"] == "switch.test_switch"


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


def test_switch_turn_on_case_insensitive(handler):
    sc = handler.build_service_call("state", " On  ")
    assert sc.service == "turn_on"


# ----------------------------------------------------------------------
# Edge cases the handler MUST NOT silently normalize
# ----------------------------------------------------------------------

def test_switch_invalid_string(handler):
    with pytest.raises(ValueError):
        handler.build_service_call("state", "abc")


def test_switch_invalid_none(handler):
    with pytest.raises(ValueError):
        handler.build_service_call("state", None)


def test_switch_invalid_list(handler):
    with pytest.raises(ValueError):
        handler.build_service_call("state", [1, 2, 3])


def test_switch_invalid_dict(handler):
    with pytest.raises(ValueError):
        handler.build_service_call("state", {"x": 1})


# ----------------------------------------------------------------------
# Unsupported point names must raise UnsupportedPointError
# ----------------------------------------------------------------------

def test_switch_unsupported_point(handler):
    with pytest.raises(UnsupportedPointError):
        handler.build_service_call("brightness", 50)


# ----------------------------------------------------------------------
# _normalize_value Tests (deep coverage)
# ----------------------------------------------------------------------

def test_normalize_state_true_cases():
    assert _normalize_value("state", "on") is True
    assert _normalize_value("state", "TRUE") is True
    assert _normalize_value("state", "  yes  ") is True
    assert _normalize_value("state", 1) is True
    assert _normalize_value("state", True) is True


def test_normalize_state_false_cases():
    assert _normalize_value("state", "off") is False
    assert _normalize_value("state", "FALSE") is False
    assert _normalize_value("state", " No ") is False
    assert _normalize_value("state", 0) is False
    assert _normalize_value("state", False) is False


def test_normalize_state_unrecognized_returns_original():
    assert _normalize_value("state", "abc") == "abc"
    assert _normalize_value("state", "stop") == "stop"
    assert _normalize_value("state", 999) == 999


def test_normalize_numeric_string():
    # should not be treated as boolean; returns original
    assert _normalize_value("state", "2") == "2"


# ----------------------------------------------------------------------
# build_service_payload Tests
# ----------------------------------------------------------------------

def test_build_payload_basic():
    payload = build_service_payload("switch.kitchen")
    assert payload == {"entity_id": "switch.kitchen"}


def test_build_payload_extra():
    payload = build_service_payload("switch.kitchen", {"level": 10})
    assert payload == {"entity_id": "switch.kitchen", "level": 10}


def test_build_payload_entity_id_cannot_be_overwritten():
    payload = build_service_payload("switch.kitchen", {"entity_id": "fake", "x": 3})
    assert payload["entity_id"] == "switch.kitchen"
    assert payload["x"] == 3