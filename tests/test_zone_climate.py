"""Tests for the MelView zone climate entities."""

from typing import Any

import pytest
from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_component import async_update_entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melview.melview import MelViewValidationError

from .conftest import MelViewApi, setup_entry

UNIT = "climate.ducted_ac"
LOUNGE = "climate.ducted_ac_zone_lounge"
BEDROOM = "climate.ducted_ac_zone_bedroom"

ON_OFF = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF


@pytest.fixture
def zone_api(melview_api: MelViewApi) -> MelViewApi:
    """Return the fake MelView API with a room sensor in every zone.

    The unit is heating, the Lounge is on and the Bedroom is off.
    """
    lounge, bedroom = melview_api.unit["zones"]
    lounge.update(temp=21.5, humidity=60, heat="20.5", cool="24", damper=128)
    bedroom.update(temp=19, humidity=65, heat="18", cool="23", damper=128)
    return melview_api


async def call(hass: HomeAssistant, service: str, entity_id: str, **data: Any) -> None:
    """Call a climate action on a zone."""
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_state(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone shows its own readings and the heating temperature while heating."""
    await setup_entry(hass, config_entry)

    state = hass.states.get(LOUNGE)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_HVAC_MODES] == [HVACMode.OFF, HVACMode.HEAT]
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 21.5
    assert state.attributes[ATTR_CURRENT_HUMIDITY] == 60
    assert state.attributes[ATTR_TEMPERATURE] == 20.5
    assert state.attributes[ATTR_TARGET_TEMP_STEP] == 0.5
    assert state.attributes[ATTR_MIN_TEMP] == 17
    assert state.attributes[ATTR_MAX_TEMP] == 28
    assert state.attributes["Spill active"] is False
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        ON_OFF | ClimateEntityFeature.TARGET_TEMPERATURE
    )

    assert hass.states.get(BEDROOM).state == HVACMode.OFF
    assert hass.states.get(BEDROOM).attributes[ATTR_TEMPERATURE] == 18


async def test_zones_without_room_sensor(
    hass: HomeAssistant, melview_api: MelViewApi, init_integration: MockConfigEntry
) -> None:
    """Zones that report no temperatures to set get no climate entity."""
    assert hass.states.get(UNIT)
    assert hass.states.get("switch.ducted_ac_zone_lounge")
    assert hass.states.get(LOUNGE) is None
    assert hass.states.get(BEDROOM) is None


async def test_cooling_shows_cool_temperature(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone shows its cooling temperature while the unit is cooling."""
    zone_api.unit["setmode"] = 3
    await setup_entry(hass, config_entry)

    state = hass.states.get(LOUNGE)
    assert state.state == HVACMode.COOL
    assert state.attributes[ATTR_TEMPERATURE] == 24
    assert state.attributes[ATTR_MIN_TEMP] == 19
    assert state.attributes[ATTR_MAX_TEMP] == 30


async def test_auto_shows_both_temperatures(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone shows a heating and a cooling temperature in auto mode."""
    zone_api.unit["setmode"] = 8
    await setup_entry(hass, config_entry)

    state = hass.states.get(LOUNGE)
    assert state.state == HVACMode.AUTO
    assert state.attributes[ATTR_TARGET_TEMP_LOW] == 20.5
    assert state.attributes[ATTR_TARGET_TEMP_HIGH] == 24
    assert ATTR_TEMPERATURE not in state.attributes
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        ON_OFF | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    )


@pytest.mark.parametrize("setmode", [2, 7])
async def test_no_temperature_in_dry_and_fan_modes(
    hass: HomeAssistant,
    zone_api: MelViewApi,
    config_entry: MockConfigEntry,
    setmode: int,
) -> None:
    """A zone has no temperature to set while the unit is drying or fanning."""
    zone_api.unit["setmode"] = setmode
    del zone_api.caps["max"]["3"]
    await setup_entry(hass, config_entry)

    state = hass.states.get(LOUNGE)
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == ON_OFF
    assert ATTR_TEMPERATURE not in state.attributes
    assert ATTR_TARGET_TEMP_LOW not in state.attributes
    # Home Assistant defaults, as the unit reports no range for the mode
    assert state.attributes[ATTR_MIN_TEMP] == 7
    assert state.attributes[ATTR_MAX_TEMP] == 35

    with pytest.raises(ServiceValidationError):
        await call(hass, SERVICE_SET_TEMPERATURE, LOUNGE, temperature=22)
    assert zone_api.commands == []


async def test_unit_off(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """Every zone is off while the unit is off."""
    zone_api.unit["power"] = 0
    await setup_entry(hass, config_entry)

    assert hass.states.get(LOUNGE).state == HVACMode.OFF


async def test_spill_zone(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone held open for spill is on with spill active."""
    zone_api.unit["zones"][0]["status"] = 2
    await setup_entry(hass, config_entry)

    state = hass.states.get(LOUNGE)
    assert state.state == HVACMode.HEAT
    assert state.attributes["Spill active"] is True


@pytest.mark.parametrize(
    ("setmode", "data", "commands"),
    [
        (1, {ATTR_TEMPERATURE: 21.5}, ["ZH121.5"]),
        (1, {ATTR_TEMPERATURE: 22}, ["ZH122"]),
        (3, {ATTR_TEMPERATURE: 25.5}, ["ZL125.5"]),
        (
            8,
            {ATTR_TARGET_TEMP_LOW: 20, ATTR_TARGET_TEMP_HIGH: 25.5},
            ["ZH120,ZL125.5"],
        ),
    ],
)
async def test_set_temperature(
    hass: HomeAssistant,
    zone_api: MelViewApi,
    config_entry: MockConfigEntry,
    setmode: int,
    data: dict[str, Any],
    commands: list[str],
) -> None:
    """The temperature for the mode the unit is in is sent for the zone."""
    zone_api.unit["setmode"] = setmode
    await setup_entry(hass, config_entry)

    await call(hass, SERVICE_SET_TEMPERATURE, LOUNGE, **data)

    assert zone_api.commands == commands


async def test_set_temperature_updates_state(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """The zone shows its new temperature straight after it is set."""
    await setup_entry(hass, config_entry)

    await call(hass, SERVICE_SET_TEMPERATURE, BEDROOM, temperature=19.5)

    assert zone_api.commands == ["ZH219.5"]
    assert hass.states.get(BEDROOM).attributes[ATTR_TEMPERATURE] == 19.5
    assert hass.states.get(LOUNGE).attributes[ATTR_TEMPERATURE] == 20.5


async def test_set_temperature_and_turn_on(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A mode given with a temperature turns the zone on first."""
    await setup_entry(hass, config_entry)

    await call(
        hass, SERVICE_SET_TEMPERATURE, BEDROOM, temperature=19, hvac_mode=HVACMode.HEAT
    )

    assert zone_api.commands == ["Z21", "ZH219"]
    assert hass.states.get(BEDROOM).state == HVACMode.HEAT


async def test_temperature_outside_range(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A temperature outside the range of the unit mode is rejected."""
    await setup_entry(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await call(hass, SERVICE_SET_TEMPERATURE, LOUNGE, temperature=29)
    assert zone_api.commands == []


async def test_set_temperature_needs_a_temperature(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """Nothing is sent for a zone when no temperature is given."""
    await setup_entry(hass, config_entry)
    device = config_entry.runtime_data[0].device

    with pytest.raises(MelViewValidationError, match="No zone temperature given"):
        await device.async_set_zone_temperature(1)
    assert zone_api.commands == []


async def test_zone_ten_is_addressed_as_zero(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """The tenth zone is sent as zone 0, as the Wi-Fi Control app does."""
    zone_api.unit["zones"].append(
        {"zoneid": 10, "name": "Study", "status": 1, "heat": "20", "cool": "24"}
    )
    await setup_entry(hass, config_entry)

    await call(
        hass, SERVICE_SET_TEMPERATURE, "climate.ducted_ac_zone_study", temperature=21
    )

    assert zone_api.commands == ["ZH021"]


@pytest.mark.parametrize(
    ("service", "entity_id", "data", "commands", "state"),
    [
        (SERVICE_TURN_ON, BEDROOM, {}, ["Z21"], HVACMode.HEAT),
        (SERVICE_TURN_OFF, LOUNGE, {}, ["Z10"], HVACMode.OFF),
        (
            SERVICE_SET_HVAC_MODE,
            BEDROOM,
            {ATTR_HVAC_MODE: HVACMode.HEAT},
            ["Z21"],
            HVACMode.HEAT,
        ),
        (
            SERVICE_SET_HVAC_MODE,
            LOUNGE,
            {ATTR_HVAC_MODE: HVACMode.OFF},
            ["Z10"],
            HVACMode.OFF,
        ),
    ],
)
async def test_turn_zone_on_and_off(
    hass: HomeAssistant,
    zone_api: MelViewApi,
    config_entry: MockConfigEntry,
    service: str,
    entity_id: str,
    data: dict[str, Any],
    commands: list[str],
    state: str,
) -> None:
    """Turning a zone on or off sends its command and updates the state."""
    await setup_entry(hass, config_entry)

    await call(hass, service, entity_id, **data)

    assert zone_api.commands == commands
    assert hass.states.get(entity_id).state == state


async def test_zone_cannot_change_the_unit_mode(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone only offers off and the mode the unit is in."""
    await setup_entry(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await call(hass, SERVICE_SET_HVAC_MODE, LOUNGE, hvac_mode=HVACMode.COOL)
    assert zone_api.commands == []


async def test_invalid_reading(
    hass: HomeAssistant,
    zone_api: MelViewApi,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reading that is not a number is unknown and logged; a missing one is unknown."""
    lounge = zone_api.unit["zones"][0]
    lounge["temp"] = "--"
    del lounge["humidity"]
    await setup_entry(hass, config_entry)

    state = hass.states.get(LOUNGE)
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] is None
    assert ATTR_CURRENT_HUMIDITY not in state.attributes
    assert "Invalid zone 1 temp value: --" in caplog.text


async def test_zone_no_longer_reported(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone the unit stops reporting is unavailable."""
    await setup_entry(hass, config_entry)

    del zone_api.unit["zones"][0]
    await async_update_entity(hass, UNIT)
    await hass.async_block_till_done()

    assert hass.states.get(LOUNGE).state == STATE_UNAVAILABLE
    assert hass.states.get(BEDROOM).state == HVACMode.OFF


async def test_action_failure_raises(
    hass: HomeAssistant, zone_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone action that cannot be sent raises an error."""
    await setup_entry(hass, config_entry)
    zone_api.status["unitcommand.aspx"] = 500

    with pytest.raises(HomeAssistantError, match="MelView command failed"):
        await call(hass, SERVICE_SET_TEMPERATURE, LOUNGE, temperature=21)
