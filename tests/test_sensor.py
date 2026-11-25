"""Tests for the MelView sensors."""

import pytest
from homeassistant.components.climate import ATTR_CURRENT_TEMPERATURE
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import async_update_entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melview.const import CONF_SENSOR, DOMAIN

from .conftest import EMAIL, MelViewApi, setup_entry

CLIMATE = "climate.ducted_ac"
ROOM_TEMP = "sensor.ducted_ac_current_temperature"


async def test_current_temperature(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The room temperature has its own sensor."""
    state = hass.states.get(ROOM_TEMP)
    assert state.state == "21.5"
    assert state.attributes["unit_of_measurement"] == "°C"
    assert state.attributes["device_class"] == "temperature"
    assert "source" not in state.attributes


async def test_sensor_option_off(
    hass: HomeAssistant, melview_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """No sensors are created when the option is off."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data=config_entry.data,
        options={**config_entry.options, CONF_SENSOR: False},
    )
    await setup_entry(hass, entry)

    assert hass.states.get(CLIMATE)
    assert hass.states.get(ROOM_TEMP) is None


async def test_missing_temperature_is_unknown(
    hass: HomeAssistant, melview_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A missing reading is unknown, not 0 °C."""
    del melview_api.unit["roomtemp"]
    await setup_entry(hass, config_entry)

    assert hass.states.get(ROOM_TEMP).state == STATE_UNKNOWN
    assert hass.states.get(CLIMATE).attributes[ATTR_CURRENT_TEMPERATURE] is None


async def test_invalid_temperature_is_unknown(
    hass: HomeAssistant,
    melview_api: MelViewApi,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reading that is not a number is unknown and logged."""
    melview_api.unit["roomtemp"] = "--"
    await setup_entry(hass, config_entry)

    assert hass.states.get(ROOM_TEMP).state == STATE_UNKNOWN
    assert "Invalid roomtemp value: --" in caplog.text


async def test_lossnay_sensors(
    hass: HomeAssistant, erv_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """Lossnay units get air temperature and core efficiency sensors."""
    await setup_entry(hass, config_entry)

    assert hass.states.get("sensor.lossnay_current_temperature").state == "22.0"
    assert hass.states.get("sensor.lossnay_fresh_air").state == "12.0"
    assert hass.states.get("sensor.lossnay_stale_air").state == "15.0"
    # Fresh air warmed by the core: 12 + 0.75 * (22 - 12)
    assert hass.states.get("sensor.lossnay_pre_warmed").state == "19.5"
    assert hass.states.get("sensor.lossnay_core_efficiency").state == "75.0"
    assert hass.states.get("climate.lossnay") is None


@pytest.mark.parametrize(
    ("missing", "unknown"),
    [
        ("outdoortemp", ["sensor.lossnay_fresh_air", "sensor.lossnay_pre_warmed"]),
        (
            "coreefficiency",
            ["sensor.lossnay_core_efficiency", "sensor.lossnay_pre_warmed"],
        ),
    ],
)
async def test_lossnay_missing_readings(
    hass: HomeAssistant,
    erv_api: MelViewApi,
    config_entry: MockConfigEntry,
    missing: str,
    unknown: list[str],
) -> None:
    """Sensors that depend on a missing reading are unknown."""
    del erv_api.unit[missing]
    await setup_entry(hass, config_entry)

    for entity_id in unknown:
        assert hass.states.get(entity_id).state == STATE_UNKNOWN
    assert hass.states.get("sensor.lossnay_stale_air").state == "15.0"


ZONE_TEMP = "sensor.ducted_ac_zone_lounge_temperature"
ZONE_HUMIDITY = "sensor.ducted_ac_zone_lounge_humidity"


@pytest.fixture
def zone_sensor_api(melview_api: MelViewApi) -> MelViewApi:
    """Return the fake MelView API with a room sensor in the Lounge zone only."""
    melview_api.unit["zones"][0].update(temp=21.5, humidity=60, heat="17.5", cool="21")
    return melview_api


async def refresh(hass: HomeAssistant) -> None:
    """Poll the unit again."""
    await async_update_entity(hass, CLIMATE)
    await hass.async_block_till_done()


async def test_zone_sensors(
    hass: HomeAssistant, zone_sensor_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone with its own room sensor gets temperature and humidity sensors."""
    await setup_entry(hass, config_entry)

    state = hass.states.get(ZONE_TEMP)
    assert state.state == "21.5"
    assert state.attributes["unit_of_measurement"] == "°C"
    assert state.attributes["device_class"] == "temperature"
    state = hass.states.get(ZONE_HUMIDITY)
    assert state.state == "60.0"
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["device_class"] == "humidity"


async def test_zone_without_room_sensor(
    hass: HomeAssistant, zone_sensor_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """A zone that reports no readings gets no sensors."""
    await setup_entry(hass, config_entry)

    assert hass.states.get("switch.ducted_ac_zone_bedroom")
    assert hass.states.get("sensor.ducted_ac_zone_bedroom_temperature") is None
    assert hass.states.get("sensor.ducted_ac_zone_bedroom_humidity") is None


async def test_zone_sensors_option_off(
    hass: HomeAssistant, zone_sensor_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """No zone sensors are created when the sensor option is off."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data=config_entry.data,
        options={**config_entry.options, CONF_SENSOR: False},
    )
    await setup_entry(hass, entry)

    assert hass.states.get(ZONE_TEMP) is None
    assert hass.states.get(ZONE_HUMIDITY) is None


async def test_zone_sensors_follow_the_unit(
    hass: HomeAssistant, zone_sensor_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """Zone sensors update with the unit, and a missing reading is unknown."""
    await setup_entry(hass, config_entry)

    zone = zone_sensor_api.unit["zones"][0]
    zone["temp"] = 19
    del zone["humidity"]
    await refresh(hass)

    assert hass.states.get(ZONE_TEMP).state == "19.0"
    assert hass.states.get(ZONE_HUMIDITY).state == STATE_UNKNOWN


async def test_zone_sensor_invalid_value(
    hass: HomeAssistant,
    zone_sensor_api: MelViewApi,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A zone reading that is not a number is unknown and logged."""
    zone_sensor_api.unit["zones"][0]["temp"] = "--"
    await setup_entry(hass, config_entry)

    assert hass.states.get(ZONE_TEMP).state == STATE_UNKNOWN
    assert "Invalid zone 1 temp value: --" in caplog.text


async def test_zone_sensors_unavailable_when_zone_disappears(
    hass: HomeAssistant, zone_sensor_api: MelViewApi, config_entry: MockConfigEntry
) -> None:
    """Zone sensors go unavailable once the unit stops reporting the zone."""
    await setup_entry(hass, config_entry)

    del zone_sensor_api.unit["zones"][0]
    await refresh(hass)

    assert hass.states.get(ZONE_TEMP).state == STATE_UNAVAILABLE
    assert hass.states.get(ZONE_HUMIDITY).state == STATE_UNAVAILABLE
