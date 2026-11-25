import logging

from homeassistant.components import logbook
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_HALVES,
    PRECISION_WHOLE,
    UnitOfTemperature,
)

from .coordinator import MelViewCoordinator
from .entity import MelViewBaseEntity, zone_data
from .melview import MODE

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def _set_mode(data: dict) -> HVACMode:
    """Return the mode a unit is set to, which it reports even when off."""
    mode_index = data.get("setmode")
    return next(
        (mode for mode, val in MODE.items() if val == mode_index), HVACMode.AUTO
    )


class MelViewClimate(MelViewBaseEntity, ClimateEntity):
    """MelView handler for Home Assistant"""

    _attr_name = None

    def __init__(self, coordinator: MelViewCoordinator):
        super().__init__(coordinator, coordinator.device)
        self._device = coordinator.device
        device = coordinator.device

        self._attr_unique_id = device.get_id()

        self._operations_list = [x for x in MODE] + [HVACMode.OFF]
        self._speeds_list = [x for x in self._device.fan_keyed]

        self._precision = PRECISION_WHOLE
        self._target_step = 1.0

    async def async_added_to_hass(self):
        """Perform async operations when entity is added to hass."""
        await super().async_added_to_hass()
        self._precision = PRECISION_WHOLE
        self._target_step = 1.0
        if getattr(self._device, "halfdeg", False):
            self._precision = PRECISION_HALVES
            self._target_step = 0.5

    @property
    def supported_features(self):
        """Let HASS know feature support"""
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._device.fan:
            features |= ClimateEntityFeature.FAN_MODE
        if self._set_mode in (
            HVACMode.AUTO,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.DRY,
        ):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def precision(self):
        """Return the precision of the system"""
        return self._precision

    @property
    def temperature_unit(self):
        """Define unit for temperature"""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> float | None:
        """Get the current room temperature"""
        return self._data_float("roomtemp")

    @property
    def target_temperature(self) -> float | None:
        """Get the target temperature"""
        return self._data_float("settemp")

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature for the current HVAC mode."""
        mode = self._set_mode
        if mode in self._device.temp_ranges:
            return self._device.temp_ranges[mode]["min"]
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature for the current HVAC mode."""
        mode = self._set_mode
        if mode in self._device.temp_ranges:
            return self._device.temp_ranges[mode]["max"]
        return super().max_temp

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature"""
        return self._target_step

    @property
    def _set_mode(self) -> HVACMode:
        """Get the configured mode, which the unit reports even when off"""
        return _set_mode(self.coordinator.data)

    @property
    def hvac_mode(self):
        """Get the current operating mode"""
        if self.coordinator.data.get("power", 0) == 0:
            return HVACMode.OFF
        return self._set_mode

    @property
    def hvac_modes(self):
        """Get possible operating modes"""
        return self._operations_list

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan speed label."""
        code = self.coordinator.data.get("setfan")
        label = self._device.fan.get(code)
        if label is None:
            _LOGGER.error("Fan code %s not present in available modes", code)
        return label

    @property
    def fan_modes(self):
        """Get the possible fan speeds"""
        return self._speeds_list

    @property
    def hvac_action(self):
        """Get the current action, returns None unless explicitly known."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.hvac_mode == HVACMode.HEAT:
            if self._device._standby:
                return HVACAction.PREHEATING
            return None
        if self.hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        return None

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature, and the operating mode if given"""
        # Set mode first so the temperature is checked against its range
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            _LOGGER.debug("Set temperature %.1f", temp)
            await self._async_command(self._device.async_set_temperature(temp))
            await self.coordinator.async_refresh()

    async def async_set_fan_mode(self, fan_mode) -> None:
        """Set the fan speed"""
        speed = fan_mode
        _LOGGER.debug("Set fan: %s", speed)
        await self._async_command(self._device.async_set_speed(speed))
        await self.coordinator.async_refresh()
        parsed_speed = fan_mode.title()
        logbook.log_entry(
            hass=self.hass,
            name=self.name,
            message=f"Fan speed set to {parsed_speed}",
            entity_id=self.entity_id,
        )

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        _LOGGER.debug("Set mode: %s", hvac_mode)
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        await self._async_command(self._device.async_set_mode(hvac_mode))
        await self.coordinator.async_refresh()

    async def async_turn_on(self) -> None:
        """Turn on the unit"""
        _LOGGER.debug("Power on")
        await self._async_command(self._device.async_power_on())
        await self.coordinator.async_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the unit"""
        _LOGGER.debug("Power off")
        await self._async_command(self._device.async_power_off())
        await self.coordinator.async_refresh()


class MelViewZoneClimate(MelViewBaseEntity, ClimateEntity):
    """Thermostat for one zone of a zone controller with room sensors (e.g. ZoneIQ).

    A zone follows the mode of its unit. It can only be turned on or off and
    given its own heating and cooling temperatures.
    """

    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = 0.5
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: MelViewCoordinator, zone):
        super().__init__(coordinator, coordinator.device)
        self._id = zone.id
        self._attr_name = f"Zone {zone.name}"
        self._attr_unique_id = f"{coordinator.device.get_id()}-{zone.id}_climate"

    @property
    def _zone(self) -> dict:
        return zone_data(self.coordinator, self._id) or {}

    def _zone_float(self, key: str) -> float | None:
        """Return a numeric zone value, or None if missing or invalid."""
        value = self._zone.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.error("Invalid zone %s %s value: %s", self._id, key, value)
            return None

    @property
    def _unit_mode(self) -> HVACMode:
        return _set_mode(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return False once the unit stops reporting this zone."""
        return super().available and zone_data(self.coordinator, self._id) is not None

    @property
    def supported_features(self):
        """Zone temperatures can be set in heat, cool and auto modes only"""
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._unit_mode in (HVACMode.HEAT, HVACMode.COOL):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        elif self._unit_mode == HVACMode.AUTO:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        return features

    @property
    def hvac_mode(self):
        """Return the unit mode while the zone is on, otherwise off"""
        if not self.coordinator.data.get("power") or not self._zone.get("status"):
            return HVACMode.OFF
        return self._unit_mode

    @property
    def hvac_modes(self):
        """A zone can only be off or in the mode the unit is set to"""
        return [HVACMode.OFF, self._unit_mode]

    @property
    def current_temperature(self) -> float | None:
        return self._zone_float("temp")

    @property
    def current_humidity(self) -> float | None:
        return self._zone_float("humidity")

    # Home Assistant only reads the target temperatures the current
    # supported_features allow: one in heat or cool mode, the range in auto.

    @property
    def target_temperature(self) -> float | None:
        return self._zone_float("heat" if self._unit_mode == HVACMode.HEAT else "cool")

    @property
    def target_temperature_low(self) -> float | None:
        return self._zone_float("heat")

    @property
    def target_temperature_high(self) -> float | None:
        return self._zone_float("cool")

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature for the mode the unit is set to."""
        if self._unit_mode in self._device.temp_ranges:
            return self._device.temp_ranges[self._unit_mode]["min"]
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature for the mode the unit is set to."""
        if self._unit_mode in self._device.temp_ranges:
            return self._device.temp_ranges[self._unit_mode]["max"]
        return super().max_temp

    @property
    def extra_state_attributes(self):
        """Return spill status as attribute."""
        return {"Spill active": self._zone.get("status") == 2}

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the zone temperature for the mode the unit is set to"""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)
        if self._unit_mode == HVACMode.AUTO:
            heat = kwargs.get(ATTR_TARGET_TEMP_LOW)
            cool = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        elif self._unit_mode == HVACMode.HEAT:
            heat, cool = kwargs.get(ATTR_TEMPERATURE), None
        else:
            heat, cool = None, kwargs.get(ATTR_TEMPERATURE)
        _LOGGER.debug("Set zone %s temperature: heat %s, cool %s", self._id, heat, cool)
        await self._async_command(
            self._device.async_set_zone_temperature(self._id, heat=heat, cool=cool)
        )
        await self.coordinator.async_refresh()

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        """Turn the zone on or off; the mode itself belongs to the unit"""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_turn_on(self) -> None:
        """Turn on the zone"""
        _LOGGER.debug("Zone %s on", self._id)
        await self._async_command(self._device.async_enable_zone(self._id))
        await self.coordinator.async_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the zone"""
        _LOGGER.debug("Zone %s off", self._id)
        await self._async_command(self._device.async_disable_zone(self._id))
        await self.coordinator.async_refresh()


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up MelView device climate based on config_entry."""
    coordinators = entry.runtime_data
    entities = [
        MelViewClimate(coordinator)
        for coordinator in coordinators
        if coordinator.device.get_unit_type() != "ERV"
    ]
    for coordinator in coordinators:
        for zone in coordinator.get_zones():
            reported = zone_data(coordinator, zone.id) or {}
            # Only zones with their own room sensor report temperatures to set
            if "heat" in reported and "cool" in reported:
                entities.append(MelViewZoneClimate(coordinator, zone))
    async_add_entities(entities)
