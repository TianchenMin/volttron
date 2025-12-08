````markdown
# Home Assistant Interface for Volttron PlatformDriver

## 1. Overview

This module implements the Home Assistant interface for the Volttron
PlatformDriver. It allows Volttron to:

- Read the current state and attributes of Home Assistant entities.
- Send service calls (turn on/off, set percentage/position, change climate modes, etc.)
  to Home Assistant over its HTTP API.

The driver relies on the PlatformDriver registry configuration (CSV) to define:

- Which Home Assistant entities are exposed to Volttron.
- Which logical "points" (state, percentage, position, temperature, brightness, etc.)
  are readable/writable.
- How Volttron point names map onto Home Assistant entity IDs and attributes.

## 2. File Locations

Key paths in the CS 5500 Volttron VM:

- Main interface implementation:

  ```text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/platform_driver/interfaces/home_assistant.py
````

On the course VM this is typically:

```text
~/volttron/services/core/PlatformDriverAgent/platform_driver/interfaces/home_assistant.py
```

* This README (recommended location):

  ```text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/platform_driver/interfaces/README_home_assistant.md
  ```

* Local unit tests for the Home Assistant interface:

  ```text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/unit_tests/test_home_assistant_handlers.py
  ```

You can assume `<VOLTTRON_ROOT>` is `~/volttron` unless your environment is customized.

## 3. Architecture

### 3.1 Core Concepts

The refactored `home_assistant.py` introduces several key abstractions and helpers.

#### Exception types

* `UnsupportedDomainError`
  Thrown when an entity’s domain (e.g., `fan`, `switch`, `cover`, `light`, `climate`)
  does not have a registered handler or the entity_id format is invalid.

* `UnsupportedPointError`
  Thrown by domain handlers when a requested logical point (e.g., `state`,
  `percentage`, `position`, `temperature`) is not supported.

* `ServiceCallError`
  Thrown when an HTTP call to Home Assistant’s `/api/services` endpoint fails
  (non-200 status code or network error). Contains the domain, service, payload
  and an error message.

#### Service call description

* `HomeAssistantServiceCall` (dataclass)
  A pure data container with fields:

  * `domain`: Home Assistant domain (e.g., `fan`, `switch`, `cover`, `light`, `climate`)
  * `service`: service name within that domain (e.g., `turn_on`,
    `turn_off`, `set_percentage`, `set_hvac_mode`, `set_temperature`)
  * `payload`: JSON-serializable dict to be sent as the HTTP body

Building a `HomeAssistantServiceCall` does **not** perform any network I/O.

#### Utility helpers

* `parse_entity_id(entity_id: str) -> (domain, object_id)`
  Validates that the entity_id is a non-empty string containing a single `.` and
  returns `(domain, object_id)`. Raises `ValueError` if the format is invalid.

* `get_domain_from_entity_id(entity_id: str) -> str`
  Convenience wrapper that returns only the domain part. Used throughout the
  driver to decide which handler to use.

* `_normalize_value(point_name: str, value: Any) -> Any`
  Best-effort normalization for common point types:

  * For `"state"`:

    * `"on"`, `"true"`, `"open"`, `1` → `True`
    * `"off"`, `"false"`, `"closed"`, `"close"`, `0` → `False`
    * Otherwise returns the original value (handlers may interpret it).
  * For numeric points (e.g., `temperature`, `position`, `percentage`,
    `brightness`, `speed`):

    * `int`/`float` → kept as-is.
    * Numeric strings → converted to `int` or `float` when possible.
    * Non-numeric strings / unsupported types → returned as-is.
  * For other points: value is returned unchanged.

* `build_service_url(base_url, domain, service)`
  Returns `<base_url>/api/services/<domain>/<service>`.

* `build_service_payload(entity_id, extra=None)`
  Always includes `"entity_id": <entity_id>` and merges any additional keys from `extra`.

#### Base class for domain handlers

* `HomeAssistantDomainHandler`
  Abstract base class that encapsulates logic for a specific Home Assistant
  domain. It holds:

  * `entity_id`: the full entity ID, e.g., `fan.living_room_fan`
  * `config`: per-entity configuration (e.g., entity_point, attributes, units)
  * Abstract method:

    * `build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall`

Subclasses implement the mapping from a logical point name + value to a
concrete Home Assistant service call. **They do not perform HTTP calls.**

### 3.2 Domain Handlers

Currently implemented domain handlers:

* **`FanHandler`** (for `fan.*` entities)

  * Supported points:

    * `"state"`:

      * Accepts `0`, `1`, `False`, `True`, `"on"`, `"off"`, `"0"`, `"1"`.
      * Maps to:

        * `fan.turn_on` when “on”
        * `fan.turn_off` when “off”
    * `"percentage"`:

      * Accepts integers in `[0, 100]` (value is cast with `int(value)`).
      * Maps to `fan.set_percentage` with payload `{"percentage": <value>}`.
  * Invalid values raise `ValueError`. Unsupported points raise `UnsupportedPointError`.

* **`SwitchHandler`** (for `switch.*` entities)

  * Supported points:

    * `"state"`:

      * Accepts `0`, `1`, `False`, `True`, `"on"`, `"off"`, `"0"`, `"1"`.
      * Maps to:

        * `switch.turn_on` when “on”
        * `switch.turn_off` when “off"`
  * Invalid values raise `ValueError`. Unsupported points raise `UnsupportedPointError`.

* **`CoverHandler`** (for `cover.*` entities: blinds, shades, garage doors)

  * Supported points:

    * `"state"`:

      * Accepts `"open"`, `"close"`, `"stop"`, or `0/1/2` (and `"0"/"1"/"2"`).
      * Maps to:

        * `cover.open_cover` / `cover.close_cover` / `cover.stop_cover`.
    * `"position"`:

      * Accepts integers in `[0, 100]` (value is cast with `int(value)`).
      * Maps to `cover.set_cover_position` with payload `{"position": <value>}`.
  * Invalid values raise `ValueError`. Unsupported points raise `UnsupportedPointError`.

* **`LightHandler`** (for `light.*` entities)

  * Supported points:

    * `"state"`:

      * Uses `_normalize_value("state", value)`:

        * Any boolean-like value (`on/off`, `true/false`, `open/closed`, `0/1`)
          is normalized to `True`/`False`.
      * Maps to:

        * `light.turn_on` when `True`
        * `light.turn_off` when `False`
    * `"brightness"`:

      * Uses `_normalize_value("brightness", value)`, then casts to `int`.
      * Accepts numeric values in `[0, 255]`.
      * Maps to `light.turn_on` with payload `{"brightness": <value>}`.
  * Invalid values (non-boolean state or out-of-range brightness) raise `ValueError`.
  * Unsupported points raise `UnsupportedPointError`.

* **`ThermostatHandler`** (for `climate.*` entities)

  * Supported points:

    * `"state"`:

      * Accepts:

        * Integer codes: `0`, `2`, `3`, `4` → `"off"`, `"heat"`, `"cool"`, `"auto"`.
        * Strings: `"off"`, `"heat"`, `"cool"`, `"auto"` (case-insensitive).
      * Maps to `climate.set_hvac_mode` with payload `{"hvac_mode": <mode_name>}`.
    * `"temperature"`:

      * Uses `_normalize_value("temperature", value)` and casts to `float`.
      * Units behavior:

        * If `config["units"] == "C"`:

          * Assumes input is Fahrenheit and converts to Celsius using the legacy
            logic from `set_thermostat_temperature`.
        * Otherwise:

          * Sends the value as-is.
      * Maps to `climate.set_temperature` with payload `{"temperature": <converted>}`.
  * Invalid state or non-numeric temperature values raise `ValueError`.
  * Unsupported points raise `UnsupportedPointError`.

* **Handler registry**

  * `HANDLER_REGISTRY: Dict[str, Type[HomeAssistantDomainHandler]]`:

    ```python
    HANDLER_REGISTRY = {
        "fan": FanHandler,
        "switch": SwitchHandler,
        "cover": CoverHandler,
        "light": LightHandler,
        "climate": ThermostatHandler,
    }
    ```

  * This maps Home Assistant domains to their corresponding handler classes.

### 3.3 Interface Write Workflow

The main entry point for writes is:

* `Interface._set_point(self, point_name, value)`

Write path:

1. Volttron calls `_set_point("Some Volttron Point Name", value)`.

2. The driver looks up a `HomeAssistantRegister` by Volttron point name.

3. It enforces the `read_only` flag and casts `value` through `register.reg_type`.

4. It inspects `register.entity_id` to determine the domain via
   `get_domain_from_entity_id(entity_id)`:

   * If the domain is in `HANDLER_REGISTRY`
     (currently `fan`, `switch`, `cover`, `light`, `climate`):

     * It obtains a handler instance via `_get_handler_for_register`.
     * It calls:

       * `handler.build_service_call(entity_point, register.value)`

         * `entity_point` comes from the registry CSV (`Entity Point`), e.g. `"state"`,
           `"percentage"`, `"position"`, `"temperature"`, `"brightness"`.
     * It receives a `HomeAssistantServiceCall` object.
     * It passes this to `_call_service(...)`, which actually performs the HTTP POST
       to Home Assistant.
   * Otherwise (e.g. `input_boolean.*` or other domains without handlers):

     * It falls back to the original “legacy” branch in `_set_point`, which still
       contains the old implementation for:

       * `light.*` (turn_on/turn_off/brightness)
       * `input_boolean.*`
       * `climate.*`

     In practice, because `light` and `climate` are now in `HANDLER_REGISTRY`,
     their traffic goes through the handler-based path; the legacy code remains as
     a compatibility fallback.

5. The register’s cached value (`register.value`) is updated.

The actual HTTP call is centralized in:

* `Interface._call_service(service_call, operation_description)`

This function:

* Builds the URL: `<base_url>/api/services/<domain>/<service>`.
* Attaches the Bearer token header using `self.access_token`.
* Sends `service_call.payload` as JSON.
* Raises `ServiceCallError` if the POST fails (exception or non-200 status code).

### 3.4 Read Workflow

The read path is largely kept from the original code:

* `Interface.get_point(point_name)`

  * Looks up the register.
  * Uses `get_entity_data(entity_id)` to fetch current JSON for the entity.
  * Returns either:

    * The top-level `"state"`, or
    * A specific attribute from `attributes[register.point_name]`.

* `Interface._scrape_all()`

  * Iterates all read/write registers.
  * For thermostats (`climate.*`), maps states to numeric codes (0, 2, 3, 4).
  * For lights and input booleans, converts `"on"/"off"` to `1/0`.
  * For other entities, returns raw state or attributes.

* `Interface.get_entity_data(entity_id)`

  * Uses `GET <base_url>/api/states/<entity_id>` to pull the JSON representation.

## 4. Implementing or Extending Domain Handlers

This section is for teammates who will implement additional logic or new domains.

### 4.1 Adding Logic to Existing Handlers

Each handler implements:

```python
class FanHandler(HomeAssistantDomainHandler):
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        ...
```

* `point_name` is the **Home Assistant-level point**, coming from the registry’s
  `Entity Point` field (e.g., `"state"`, `"percentage"`, `"position"`, `"temperature"`).
* `value` is already cast to the appropriate Python type via `register.reg_type`,
  but handlers may further normalize it (e.g., `int(value)` or `_normalize_value(...)`).

When extending:

1. Decide which logical points to support (string keys like `"mode"`, `"speed"`, etc.).
2. For each point:

   * Validate `value` (type checks, ranges).
   * Compute the appropriate Home Assistant service (domain + service).
   * Build the payload via:

     * `build_service_payload(self.entity_id, extra_dict)`.
3. Return a `HomeAssistantServiceCall(domain=<domain>, service=<service>, payload=<payload>)`.
4. For unsupported points:

   * Raise `UnsupportedPointError(point_name, self.entity_id)`.

### 4.2 Adding a New Domain (Example: MediaPlayerHandler)

If the team wants to support a new domain (e.g., `media_player.*`) via the handler
architecture:

1. Implement a new handler:

```python
class MediaPlayerHandler(HomeAssistantDomainHandler):
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            # map values to media_player.turn_on / media_player.turn_off
            ...
        elif point_name == "volume":
            # map values to media_player.volume_set with "volume_level" in payload
            ...
        else:
            raise UnsupportedPointError(point_name, self.entity_id)
```

2. Register it in `HANDLER_REGISTRY`:

```python
HANDLER_REGISTRY = {
    "fan": FanHandler,
    "switch": SwitchHandler,
    "cover": CoverHandler,
    "light": LightHandler,
    "climate": ThermostatHandler,
    "media_player": MediaPlayerHandler,  # new
}
```

3. Once registered, any `media_player.*` entity in the registry CSV will
   automatically route through this handler in `_set_point`.

### 4.3 Small Pseudo-code Example

A minimal mapping pattern looks like this:

```python
if point_name == "state":
    if value in (1, True, "on", "1"):
        service = "turn_on"
    elif value in (0, False, "off", "0"):
        service = "turn_off"
    else:
        raise ValueError("Invalid value for state")

    payload = build_service_payload(self.entity_id)
    return HomeAssistantServiceCall(domain="switch", service=service, payload=payload)
```

No HTTP calls are performed here — just pure logic.

## 5. Configuration & Registry Notes

The PlatformDriver registry CSV defines each point. Common columns:

* `Entity ID`
  e.g., `fan.living_room_fan`, `switch.kitchen_outlet`, `cover.garage_door`,
  `light.living_room`, `climate.living_room_thermostat`.

* `Entity Point`
  e.g., `state`, `percentage`, `position`, `temperature`, `brightness`.
  This is what the handler sees as `point_name`.

* `Volttron Point Name`
  The external name used in Volttron. `_set_point` is called with this
  name, and it looks up the corresponding `HomeAssistantRegister`.

* `Writable`
  `"true"` or `"false"` (case-insensitive). Non-`"true"` means the point is treated
  as read-only.

* `Units`
  e.g., `C`, `F`, percent, etc. `ThermostatHandler` uses this to decide whether
  to convert Fahrenheit to Celsius.

* `Type`
  One of `string`, `int`, `integer`, `float`, `bool`, `boolean`. This is
  mapped via `type_mapping` and used to cast values before passing them to handlers.

During `parse_config`:

* A `HomeAssistantRegister` is created for each row.
* Handlers for supported domains (currently `fan`, `switch`, `cover`, `light`, `climate`)
  are pre-instantiated and cached in `_entity_handlers`.

## 6. Limitations & Future Work

Current status:

* The handler-based architecture now covers:

  * `fan.*` (FanHandler)
  * `switch.*` (SwitchHandler)
  * `cover.*` (CoverHandler)
  * `light.*` (LightHandler)
  * `climate.*` (ThermostatHandler)
* `input_boolean.*` and other domains still rely on the legacy logic inside `_set_point`.
* The read path (`_scrape_all` / `get_point`) still uses the original implementation
  and is not yet fully handler-based.

Unit tests:

* There is a dedicated test file for the handler and helper logic:

  ```text
  services/core/PlatformDriverAgent/unit_tests/test_home_assistant_handlers.py
  ```

* You can run it with:

  ```bash
  cd ~/volttron/services/core/PlatformDriverAgent
  pytest unit_tests -q
  ```

Potential future improvements:

* Add handlers for additional domains as needed (`media_player`, `sensor`, etc.).
* Migrate more legacy write logic into handler classes to unify the write path.
* Consider making the read path handler-based where it makes sense.
* Add more unit tests around:

  * `Interface._set_point` routing behavior
  * Error handling (UnsupportedDomainError, UnsupportedPointError, ServiceCallError)
  * Registry parsing and handler registration edge cases

## 7. Development Tips for CS 5500 Teammates

* When debugging write behavior:

  * Start in `Interface._set_point`.
  * Check which branch you are hitting:

    * Handler-based (domain in `HANDLER_REGISTRY`)
    * Legacy (for `input_boolean.*` or unsupported domains).
  * Log `entity_id`, `entity_point`, and `value` if needed.

* When debugging HTTP issues:

  * Look at `_call_service` and the error logs around `ServiceCallError`.
  * Confirm:

    * `_base_url` is correct.
    * Access token is valid.
    * The Home Assistant service (`domain/service`) actually exists.

* When extending handlers:

  * Keep them **pure** — no network I/O.
  * Use small, clear helper code and explicit validation.
  * Raise `UnsupportedPointError` for unknown points so bugs fail loudly.

* When changing the registry CSV:

  * Double-check:

    * `Entity ID` format: must include a `.` separating domain and object ID.
    * `Entity Point` matches what the handler expects (`state`, `percentage`,
      `position`, `brightness`, `temperature`, etc.).
    * `Writable` is correct (write attempts will be rejected if it is not `"true"`).
    * `Type` matches your intended value type.

This README is meant as a living document. Please update it as you add new
domains, handlers, or behaviors to the Home Assistant driver.

```
::contentReference[oaicite:0]{index=0}
```
