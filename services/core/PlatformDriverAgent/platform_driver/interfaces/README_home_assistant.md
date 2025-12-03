# Home Assistant Interface for Volttron PlatformDriver

## 1. Overview

This module implements the Home Assistant interface for the Volttron
PlatformDriver. It allows Volttron to:

- Read the current state and attributes of Home Assistant entities.
- Send service calls (turn on/off, set percentage/position, change climate modes, etc.)
  to Home Assistant over its HTTP API.

The driver relies on the PlatformDriver registry configuration (CSV) to define:

- Which Home Assistant entities are exposed to Volttron.
- Which logical "points" (state, percentage, position, etc.) are readable/writable.
- How Volttron point names map onto Home Assistant entity IDs and attributes.

## 2. File Locations

Key paths in the CS 5500 Volttron VM:

- Main interface implementation:

  \`\`\`text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/platform_driver/interfaces/home_assistant.py
  \`\`\`

  On the course VM this is typically:

  \`\`\`text
  ~/volttron/services/core/PlatformDriverAgent/platform_driver/interfaces/home_assistant.py
  \`\`\`

- This README (recommended location):

  \`\`\`text
  <VOLTTRON_ROOT>/services/core/PlatformDriverAgent/platform_driver/interfaces/README_home_assistant.md
  \`\`\`

You can assume \`<VOLTTRON_ROOT>\` is \`~/volttron\` unless your environment is customized.

## 3. Architecture

### 3.1 Core Concepts

The refactored \`home_assistant.py\` introduces several key abstractions:

- **Exception types**
  - \`UnsupportedDomainError\`  
    Thrown when an entity's domain (e.g., \`fan\`, \`switch\`, \`cover\`) does not
    have a registered handler.
  - \`UnsupportedPointError\`  
    Thrown by domain handlers when a requested logical point (e.g., \`state\`,
    \`percentage\`, \`position\`) is not supported.
  - \`ServiceCallError\`  
    Thrown when an HTTP call to Home Assistant's \`/api/services\` endpoint fails
    (non-200 status code or network error).

- **Service call description**
  - \`HomeAssistantServiceCall\` (dataclass)  
    A pure data container with fields:
    - \`domain\`: Home Assistant domain (e.g., \`fan\`, \`switch\`, \`cover\`)
    - \`service\`: service name within that domain (e.g., \`turn_on\`,
      \`turn_off\`, \`set_percentage\`)
    - \`payload\`: JSON-serializable dict to be sent as the HTTP body

  The important point: building a \`HomeAssistantServiceCall\` does **not**
  perform any network I/O.

- **Base class for domain handlers**
  - \`HomeAssistantDomainHandler\`  
    Abstract base class that encapsulates logic for a specific Home Assistant
    domain. It holds:
    - \`entity_id\`: the full entity ID, e.g., \`fan.living_room_fan\`
    - \`config\`: per-entity configuration (e.g., entity_point, attributes, units)
    - Abstract method:
      - \`build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall\`

    Subclasses implement the mapping from a logical point name + value to a
    concrete Home Assistant service call.

### 3.2 Domain Handlers

Currently implemented domain handlers:

- **\`FanHandler\`** (for \`fan.*\` entities)
  - Supported points:
    - \`"state"\`:
      - Accepts \`0\`, \`1\`, \`False\`, \`True\`, \`"on"\`, \`"off"\`, \`"0"\`, \`"1"\`
      - Maps to:
        - \`fan.turn_on\` when “on”
        - \`fan.turn_off\` when “off”
    - \`"percentage"\`:
      - Accepts integer in \`[0, 100]\`
      - Maps to \`fan.set_percentage\` with payload \`{"percentage": <value>}\`
  - Implementation:
    - Uses \`build_service_payload(self.entity_id, extra)\` to build payload.
    - Returns \`HomeAssistantServiceCall(domain="fan", service=..., payload=...)\`.
    - Raises \`ValueError\` for invalid values.
    - Raises \`UnsupportedPointError\` for unsupported points.

- **\`SwitchHandler\`** (for \`switch.*\` entities)
  - Supported points:
    - \`"state"\`:
      - Accepts \`0\`, \`1\`, \`False\`, \`True\`, \`"on"\`, \`"off"\`, \`"0"\`, \`"1"\`
      - Maps to:
        - \`switch.turn_on\` when “on”
        - \`switch.turn_off\` when “off”

- **\`CoverHandler\`** (for \`cover.*\` entities: blinds, shades, garage doors)
  - Supported points:
    - \`"state"\`:
      - Accepts \`"open"\`, \`"close"\`, \`"stop"\`, or \`0/1/2\` (and \`"0"/"1"/"2"\`)
      - Maps to:
        - \`cover.open_cover\` / \`cover.close_cover\` / \`cover.stop_cover\`
    - \`"position"\`:
      - Accepts integer in \`[0, 100]\`
      - Maps to \`cover.set_cover_position\` with payload \`{"position": <value>}\`

- **Handler registry**
  - \`HANDLER_REGISTRY: Dict[str, Type[HomeAssistantDomainHandler]]\`
    - \`"fan": FanHandler\`
    - \`"switch": SwitchHandler\`
    - \`"cover": CoverHandler\`
  - This maps Home Assistant domains to their corresponding handler classes.

### 3.3 Interface Write Workflow

The main entry point for writes is:

- \`Interface._set_point(self, point_name, value)\`

Write path:

1. Volttron calls \`_set_point("Some Volttron Point Name", value)\`.
2. The driver looks up a \`HomeAssistantRegister\` by Volttron point name.
3. It enforces the \`read_only\` flag and casts \`value\` through \`register.reg_type\`.
4. It inspects \`register.entity_id\` to determine the domain:
   - If the domain is in \`HANDLER_REGISTRY\` (currently \`fan\`, \`switch\`, \`cover\`):
     - It obtains a handler instance via \`_get_handler_for_register\`.
     - It calls:
       - \`handler.build_service_call(entity_point, register.value)\`
         - \`entity_point\` comes from the registry CSV (\`Entity Point\`, e.g. \`"state"\`, \`"percentage"\`, \`"position"\`).
     - It receives a \`HomeAssistantServiceCall\` object.
     - It passes this to \`_call_service(...)\`, which actually performs the HTTP POST to Home Assistant.
   - Otherwise (e.g. \`light.*\`, \`input_boolean.*\`, \`climate.*\`):
     - It falls back to the original “legacy” branch, which:
       - For lights:
         - \`state\` → \`light.turn_on\` / \`light.turn_off\`
         - \`brightness\` → \`light.turn_on\` with \`brightness\` field
       - For input_boolean:
         - \`state\` → \`input_boolean.turn_on\` / \`input_boolean.turn_off\`
       - For climate:
         - \`state\` → hvac mode mapping (\`off\`, \`heat\`, \`cool\`, \`auto\`)
         - \`temperature\` → \`climate.set_temperature\`

5. The register's cached value is updated.

The actual HTTP call is centralized in:

- \`Interface._call_service(service_call, operation_description)\`

This function:

- Builds the URL: \`<base_url>/api/services/<domain>/<service>\`
- Attaches the Bearer token header.
- Sends \`service_call.payload\` as JSON.
- Raises \`ServiceCallError\` if the POST fails.

### 3.4 Read Workflow

The read path is largely kept from the original code:

- \`Interface.get_point(point_name)\`
  - Looks up the register.
  - Uses \`get_entity_data(entity_id)\` to fetch current JSON for the entity.
  - Returns either:
    - The top-level \`state\`, or
    - A specific attribute from \`attributes[register.point_name]\`.

- \`Interface._scrape_all()\`
  - Iterates all read/write registers.
  - For thermostats (\`climate.*\`), maps states to numeric codes (0, 2, 3, 4).
  - For lights and input booleans, converts \`on/off\` to 1/0.
  - For other entities, returns raw state or attributes.

- \`Interface.get_entity_data(entity_id)\`
  - Uses \`GET <base_url>/api/states/<entity_id>\` to pull the JSON representation.

## 4. Implementing or Extending Domain Handlers

This section is for teammates who will implement additional logic or new domains.

### 4.1 Adding Logic to Existing Handlers

Each handler implements:

\`\`\`python
class FanHandler(HomeAssistantDomainHandler):
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        ...
\`\`\`

- \`point_name\` is the **Home Assistant-level point**, coming from the registry's
  \`Entity Point\` field (e.g., \`"state"\`, \`"percentage"\`).
- \`value\` is already cast to the appropriate Python type via \`register.reg_type\`,
  but handlers may further normalize it (e.g., \`int(value)\`).

When extending:

1. Decide which logical points to support (string keys like \`"mode"\`, \`"speed"\`, etc.).
2. For each point:
   - Validate \`value\` (type checks, ranges).
   - Compute the appropriate Home Assistant service (domain + service).
   - Build the payload via:
     - \`build_service_payload(self.entity_id, extra_dict)\`
3. Return a \`HomeAssistantServiceCall(domain=<domain>, service=<service>, payload=<payload>)\).
4. For unsupported points:
   - Raise \`UnsupportedPointError(point_name, self.entity_id)\`.

### 4.2 Adding a New Domain (Example: LightHandler)

If the team wants to migrate lights to the new handler architecture:

1. Implement a new handler:

\`\`\`python
class LightHandler(HomeAssistantDomainHandler):
    def build_service_call(self, point_name: str, value: Any) -> HomeAssistantServiceCall:
        if point_name == "state":
            # map to light.turn_on / light.turn_off
            ...
        elif point_name == "brightness":
            # map to light.turn_on with brightness in payload
            ...
        else:
            raise UnsupportedPointError(point_name, self.entity_id)
\`\`\`

2. Register it in \`HANDLER_REGISTRY\`:

\`\`\python
HANDLER_REGISTRY = {
    "fan": FanHandler,
    "switch": SwitchHandler,
    "cover": CoverHandler,
    "light": LightHandler,  # new
}
\`\`\`

3. Update the legacy branch in \`_set_point\` if you want to fully migrate lights
   to the handler-based path (or keep it as a fallback).

### 4.3 Small Pseudo-code Example

A minimal mapping pattern looks like this:

\`\`\`python
if point_name == "state":
    if value in (1, True, "on", "1"):
        service = "turn_on"
    elif value in (0, False, "off", "0"):
        service = "turn_off"
    else:
        raise ValueError("Invalid value for state")

    payload = build_service_payload(self.entity_id)
    return HomeAssistantServiceCall(domain="switch", service=service, payload=payload)
\`\`\`

No HTTP calls are performed here — just pure logic.

## 5. Configuration & Registry Notes

The PlatformDriver registry CSV defines each point. Common columns:

- \`Entity ID\`  
  e.g., \`fan.living_room_fan\`, \`switch.kitchen_outlet\`, \`cover.garage_door\`

- \`Entity Point\`  
  e.g., \`state\`, \`percentage\`, \`position\`, \`temperature\`, \`brightness\`  
  This is what the handler sees as \`point_name\`.

- \`Volttron Point Name\`  
  The external name used in Volttron. \`Interface._set_point\` is called with this
  name, and it looks up the corresponding \`HomeAssistantRegister\`.

- \`Writable\`  
  \`true\` or \`false\` (case-insensitive). Non-true means the point is treated as
  read-only.

- \`Units\`  
  e.g., \`C\`, \`F\`, percent, etc.

- \`Type\`  
  One of \`string\`, \`int\`, \`integer\`, \`float\`, \`bool\`, \`boolean\`. This is
  mapped via \`type_mapping\` and used to cast values before passing them to handlers.

During \`parse_config\`:

- A \`HomeAssistantRegister\` is created for each row.
- Handlers for supported domains (fan/switch/cover) are pre-instantiated and cached.

## 6. Limitations & Future Work

Current limitations:

- Only \`fan.*\`, \`switch.*\`, and \`cover.*\` entities are handled via the new
  domain handler architecture.
- Lights (\`light.*\`), input booleans (\`input_boolean.*\`), and climates
  (\`climate.*\`) still rely on the original “legacy” logic inside \`_set_point\`.
- There is no dedicated test suite in this directory yet; most validation is done
  by running Volttron and exercising the PlatformDriver.

Potential future improvements:

- Add handlers for:
  - \`light.*\` (LightHandler)
  - \`climate.*\` (ClimateHandler)
  - Other Home Assistant domains as needed.
- Migrate existing legacy logic into handler classes to unify the write path.
- Add unit tests for:
  - Domain handler mappings
  - Error handling (UnsupportedPointError, ServiceCallError)
  - Registry parsing and handler registration

## 7. Development Tips for CS 5500 Teammates

- When debugging write behavior:
  - Start in \`Interface._set_point\`.
  - Check which branch you are hitting:
    - Handler-based (\`fan/switch/cover\`) vs. legacy (light/input_boolean/climate).
  - Print/log the \`entity_id\`, \`entity_point\`, and \`value\` if needed.

- When debugging HTTP issues:
  - Look at \`_call_service\` and the error logs around \`ServiceCallError\`.
  - Confirm:
    - \`_base_url\` is correct.
    - Access token is valid.
    - The Home Assistant service (\`domain/service\`) actually exists.

- When extending handlers:
  - Keep them **pure** — no network I/O.
  - Use small, clear helper code and explicit validation.
  - Raise \`UnsupportedPointError\` for unknown points so bugs fail loudly.

- When changing the registry CSV:
  - Double-check:
    - \`Entity ID\` format: must include a \`.\` separating domain and object ID.
    - \`Entity Point\` matches what the handler expects (\`state\`, \`percentage\`, etc.).
    - \`Writable\` is correct (write attempts will be rejected if it is not "true").

This README is meant as a living document. Please update it as you add new
domains, handlers, or behaviors to the Home Assistant driver.
