# Home Assistant Interface for Volttron PlatformDriver

## 1. Overview

This module implements the Home Assistant interface for the Volttron
PlatformDriver. It allows Volttron to:

* Read the current state and attributes of Home Assistant entities.
* Send service calls (turn on/off, set percentage or position, change climate
  modes, adjust brightness/temperature, etc.) to Home Assistant over its HTTP API.

The driver relies on the PlatformDriver registry configuration (CSV) to define:

* Which Home Assistant entities are exposed to Volttron.
* Which logical “points” (for example `state`, `percentage`, `position`,
  `brightness`, `temperature`) are readable and/or writable.
* How Volttron point names map onto Home Assistant entity IDs and attributes.

## 2. File Locations

Key paths in the CS 5500 Volttron VM:

* **Main interface implementation**

  ```text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/platform_driver/interfaces/home_assistant.py
  ```

  On the course VM this is typically:

  ```text
  ~/volttron/services/core/PlatformDriverAgent/platform_driver/interfaces/home_assistant.py
  ```

* **This README (recommended location)**

  ```text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/platform_driver/interfaces/README_home_assistant.md
  ```

You can assume `<VOLTTRON_ROOT>` is `~/volttron` unless your environment is
customized.

## 3. Architecture

### 3.1 Core Concepts

The refactored `home_assistant.py` introduces several key abstractions.

#### Exception types

* **`UnsupportedDomainError`**
  Raised when an entity’s domain (for example `fan`, `switch`, `cover`) does not
  have a registered handler.

* **`UnsupportedPointError`**
  Raised by domain handlers when a requested logical point (for example `state`,
  `percentage`, `position`, `brightness`, `temperature`) is not supported.

* **`ServiceCallError`**
  Raised when an HTTP call to Home Assistant’s `/api/services` endpoint fails
  (non-200 status code or network error).

#### Service call description

* **`HomeAssistantServiceCall` (dataclass)**
  A pure data container with fields:

  * `domain`: Home Assistant domain (for example `fan`, `switch`, `cover`,
    `light`, `climate`)
  * `service`: service name within that domain (for example `turn_on`,
    `turn_off`, `set_percentage`, `set_cover_position`, `set_temperature`,
    `set_hvac_mode`)
  * `payload`: JSON-serializable dictionary to be sent as the HTTP body

Creating a `HomeAssistantServiceCall` does **not** perform any network I/O.
All HTTP logic is centralized in the interface class.

#### Base class for domain handlers

* **`HomeAssistantDomainHandler`**
  Abstract base class that encapsulates logic for a specific Home Assistant
  domain. It holds:

  * `entity_id`: the full entity ID (for example `fan.living_room_fan`)
  * `config`: per-entity configuration (for example entity point, attributes,
    units)
  * one abstract method:

    ```python
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        ...
    ```

Subclasses implement the mapping from a logical point name plus value to a
concrete Home Assistant service call.

### 3.2 Domain Handlers

The refactored interface uses dedicated handler classes per domain.

#### `FanHandler` (for `fan.*` entities)

* **Supported points**

  * `state`
    Accepts `0`, `1`, `False`, `True`, `"on"`, `"off"`, `"0"`, `"1"`
    Maps to:

    * `fan.turn_on` when the value represents “on”
    * `fan.turn_off` when the value represents “off”

  * `percentage`
    Accepts an integer in `[0, 100]` (numeric strings are normalized).
    Maps to `fan.set_percentage` with payload `{"percentage": <value>}`.

* **Behavior**

  * Uses `build_service_payload(self.entity_id, extra)` to build the payload.
  * Returns `HomeAssistantServiceCall(domain="fan", service=..., payload=...)`.
  * Raises `ValueError` for out-of-range or invalid values.
  * Raises `UnsupportedPointError` for unsupported points.

#### `SwitchHandler` (for `switch.*` entities)

* **Supported points**

  * `state`
    Accepts multiple on/off representations:

    * On: `1`, `"1"`, `True`, `"true"`, `"on"`
    * Off: `0`, `"0"`, `False`, `"false"`, `"off"`

    Maps to:

    * `switch.turn_on` when normalized value is **True**
    * `switch.turn_off` when normalized value is **False**

* **Behavior**

  * Enforces strict validation: any other value raises `ValueError`.
  * Raises `UnsupportedPointError` for unsupported points (for example `brightness`).
  * Returns a `HomeAssistantServiceCall` with domain `"switch"` and the
    calculated service.

#### `CoverHandler` (for `cover.*` entities: blinds, shades, garage doors)

* **Supported points**

  * `state`
    Accepts semantic and numeric representations:

    * `"open"`, `"close"`, `"stop"`
    * `0`, `1`, `2` and `"0"`, `"1"`, `"2"` (commonly mapped to close/open/stop)

    Maps to:

    * `cover.open_cover`
    * `cover.close_cover`
    * `cover.stop_cover`

  * `position`
    Accepts an integer in `[0, 100]` (numeric strings are normalized).
    Maps to `cover.set_cover_position` with payload `{"position": <value>}`.

* **Behavior**

  * Raises `ValueError` for out-of-range positions or unknown state values.
  * Raises `UnsupportedPointError` for unsupported points.

#### `LightHandler` (for `light.*` entities)

* **Supported points**

  * `state`
    Same normalization as `SwitchHandler`:

    * On: `1`, `"1"`, `True`, `"true"`, `"on"`
    * Off: `0`, `"0"`, `False`, `"false"`, `"off"`

    Maps to:

    * `light.turn_on` when normalized value is **True**
    * `light.turn_off` when normalized value is **False**

  * `brightness`
    Accepts an integer in a valid Home Assistant brightness range
    (typically `[0, 255]`).
    Maps to `light.turn_on` with payload `{"brightness": <value>}`.

* **Behavior**

  * Raises `ValueError` for invalid brightness values or unknown state strings.
  * Raises `UnsupportedPointError` for unsupported points.

#### `ThermostatHandler` (for `climate.*` entities)

* **Supported points**

  * `state` (HVAC mode)

    * Accepts either numeric codes or strings.

    * Numeric example (for compatibility):

      * `0` → `"off"`
      * `2` → `"heat"`
      * `3` → `"cool"`
      * `4` → `"auto"`

    * String example:

      * `"off"`, `"heat"`, `"cool"`, `"auto"` (case-insensitive)

    Maps to `climate.set_hvac_mode` with payload `{"hvac_mode": <mode>}`.

  * `temperature`

    * Accepts numeric values or numeric strings.
    * If the per-entity config specifies units `"C"`, the handler can interpret
      input as Fahrenheit and convert to Celsius before sending to Home Assistant
      (depending on configuration).
    * Maps to `climate.set_temperature` with payload `{"temperature": <value>}`.

* **Behavior**

  * Performs numeric validation; invalid strings raise `ValueError`.
  * Raises `UnsupportedPointError` for unsupported points.

#### Handler registry

Handlers are registered in a central mapping:

```python
HANDLER_REGISTRY = {
    "fan": FanHandler,
    "switch": SwitchHandler,
    "cover": CoverHandler,
    "light": LightHandler,
    "climate": ThermostatHandler,
}
```

This registry maps Home Assistant domains to their corresponding handler classes
and is used by the interface to select the right handler at runtime.

### 3.3 Write Workflow

The main entry point for writes is:

* `Interface._set_point(self, point_name, value)`

Write path:

1. Volttron calls `_set_point("Some Volttron Point Name", value)`.

2. The driver looks up a `HomeAssistantRegister` by Volttron point name.

3. It enforces the `read_only` flag and casts `value` through
   `register.reg_type` (using the type defined in the registry CSV).

4. It inspects `register.entity_id` to determine the domain.
   If the domain is in `HANDLER_REGISTRY`:

   * It obtains a handler instance via `_get_handler_for_register`.

   * It calls:

     ```python
     handler.build_service_call(entity_point, register.value)
     ```

     where `entity_point` is the CSV `Entity Point` (for example `state`,
     `percentage`, `position`, `brightness`, `temperature`).

   * It receives a `HomeAssistantServiceCall` object.

   * It passes this to `_call_service(...)`, which performs the HTTP POST to
     Home Assistant.

   For domains that are not yet migrated to handlers, the code falls back to
   legacy logic in `_set_point` (if applicable).

5. The register’s cached value is updated on success.

The actual HTTP call is centralized in:

* `Interface._call_service(service_call, operation_description)`

This function:

* Builds the URL: `<base_url>/api/services/<domain>/<service>`.
* Attaches the Bearer token header.
* Sends `service_call.payload` as JSON.
* Raises `ServiceCallError` if the POST fails.

### 3.4 Read Workflow

The read path is largely preserved from the original implementation:

* **`Interface.get_point(point_name)`**

  * Looks up the register.
  * Uses `get_entity_data(entity_id)` to fetch current JSON for the entity via
    `GET <base_url>/api/states/<entity_id>`.
  * Returns either:

    * The top-level `state`, or
    * A specific attribute from `attributes[register.point_name]`.

* **`Interface._scrape_all()`**

  * Iterates all read/write registers.
  * For climate entities (`climate.*`), maps HVAC modes to numeric codes when
    needed for compatibility.
  * For lights and input booleans, converts `"on"/"off"` to `1/0` where expected.
  * For other entities, returns raw state or attributes.

* **`Interface.get_entity_data(entity_id)`**

  * Performs the HTTP GET and caches responses to avoid excessive network calls.

## 4. Input Normalization

A unified normalization layer avoids duplicating logic across handlers.

### 4.1 State normalization rules

Boolean-like inputs are normalized to canonical `True` / `False` values:

* Values interpreted as **True**:

  * `"on"`, `"true"`, `"1"`, `1`, `True`

* Values interpreted as **False**:

  * `"off"`, `"false"`, `"0"`, `0`, `False`

If a value cannot be clearly interpreted as representing a boolean state
(for example `"maybe"` or `"abc"`), handlers raise `ValueError`.
For some domains, semantic strings like `"open"`, `"close"`, `"stop"` are handled
explicitly by the corresponding handler.

### 4.2 Numeric normalization

For numeric points such as `percentage`, `position`, `temperature`,
`_normalize_value` attempts to parse numeric strings into `int` or `float`
values when appropriate. Invalid numeric strings are left unchanged so that
handlers can decide whether to raise an error.

This normalization is coordinated with Volttron’s `reg_type` casting, which is
based on the `Type` column in the registry CSV.

### 4.3 Benefits

* Prevents inconsistencies between domains.
* Simplifies future maintenance and refactoring.
* Ensures reproducible behavior for every write request.
* Reduces debugging time by failing fast on malformed inputs.

## 5. Configuration & Registry Notes

The PlatformDriver registry CSV defines each point. Common columns include:

* **`Entity ID`**
  Example: `fan.living_room_fan`, `switch.kitchen_outlet`,
  `cover.garage_door`, `light.kitchen`, `climate.hvac`.

* **`Entity Point`**
  Example: `state`, `percentage`, `position`, `temperature`, `brightness`.
  This is what the handler sees as `point_name`.

* **`Volttron Point Name`**
  The external name used in Volttron. `Interface._set_point` is called with
  this name and looks up the corresponding `HomeAssistantRegister`.

* **`Writable`**
  `"true"` or `"false"` (case-insensitive). Any value other than a truthy
  representation is treated as read-only and write attempts will be rejected.

* **`Units`**
  Example: `"C"`, `"F"`, `"%"`, etc. Used by some handlers (for example
  thermostats).

* **`Type`**
  One of `string`, `int`, `integer`, `float`, `bool`, `boolean`. This is mapped
  via `type_mapping` and used to cast values before passing them to handlers.

During `parse_config`:

* A `HomeAssistantRegister` is created for each CSV row.
* Handlers for supported domains (`fan`, `switch`, `cover`, `light`, `climate`)
  can be instantiated and cached for efficient access.

## 6. Unit Tests

### 6.1 Test coverage

The unit test suite validates:

* Utility functions:

  * `parse_entity_id`
  * `get_domain_from_entity_id`
  * `build_service_payload`
  * `_normalize_value`

* Domain handlers:

  * `FanHandler`: `state` and `percentage` mapping, range validation.
  * `SwitchHandler`: normalization of on/off values and rejection of invalid
    inputs or unsupported points.
  * `CoverHandler`: handling of `open` / `close` / `stop` and `position`, with
    strict range checking.
  * `LightHandler`: on/off mapping and brightness range validation.
  * `ThermostatHandler`: HVAC mode mapping (codes and strings) and temperature
    validation and conversion.

Tests assert that:

* Correct `HomeAssistantServiceCall` objects are created with:

  * The expected `domain`.
  * The expected `service`.
  * A payload that contains the correct `entity_id` and any extra fields
    (for example `percentage`, `position`, `brightness`, `temperature`,
    `hvac_mode`).

* Invalid inputs raise either `ValueError` or `UnsupportedPointError` as
  appropriate.

### 6.2 Test location

Tests are located under:

```text
services/core/PlatformDriverAgent/tests/test_home_assistant_handlers.py
```

### 6.3 How to run the tests

From the Volttron root directory:

```bash
cd ~/volttron
source env/bin/activate

pytest services/core/PlatformDriverAgent/tests/test_home_assistant_handlers.py -q
```

This runs only the Home Assistant handler tests and provides a quick signal that
the refactored interface and normalization logic behave as expected.

## 7. Limitations and Future Work

Current limitations:

* Only selected Home Assistant domains are covered by dedicated handlers
  (`fan`, `switch`, `cover`, `light`, `climate`). Other domains, if used, may
  still rely on legacy logic or may not yet be supported.
* Some advanced Home Assistant features (for example complex climate presets,
  scenes, scripts) are not modeled explicitly and would require new handler
  classes.

Potential future improvements:

* Add handler implementations for additional domains (for example
  `input_boolean`, `media_player`, and others).
* Migrate remaining legacy write logic into handler classes to unify the write
  path.
* Extend the test suite with:

  * More edge cases for normalization.
  * Tests for additional domains as they are implemented.

## 8. Development Tips for CS 5500 Teammates

* **Debugging write behavior**

  * Start in `Interface._set_point`.

  * Check which branch you are hitting:

    * Handler-based (`fan` / `switch` / `cover` / `light` / `climate`), or
    * Any remaining legacy path.

  * Log or print `entity_id`, `entity_point`, `value`, and the resulting
    `HomeAssistantServiceCall` while developing.

* **Debugging HTTP issues**

  * Look at `_call_service` and the error logs around `ServiceCallError`.
  * Confirm that:

    * `_base_url` is correct.
    * The access token is valid.
    * The Home Assistant service (`domain/service`) exists and is spelled
      correctly.

* **Extending handlers**

  * Keep handlers **pure**: they should only construct
    `HomeAssistantServiceCall` objects and must not perform network I/O.
  * Use explicit, defensive validation and raise `UnsupportedPointError` for
    unknown points so bugs fail loudly and clearly.

* **Editing the registry CSV**

  * Verify that:

    * `Entity ID` includes a dot separating domain and object ID.
    * `Entity Point` matches what the handler expects (`state`, `percentage`,
      `position`, `brightness`, `temperature`, etc.).
    * `Writable` is correct for the intended usage.
    * `Type` and `Units` values are compatible with handler expectations.

This README is intended to be a living document. Please update it as you add new
domains, handlers, or behaviors to the Home Assistant driver.