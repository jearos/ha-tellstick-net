"""Diagnostics download: device state, known sensors, unsupported devices heard."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant

from . import TellStickConfigEntry
from .const import CONF_HOUSE, CONF_MAC

# Diagnostics end up in public issues. A house code is enough for anyone in
# radio range to switch that receiver, and default names contain it.
TO_REDACT = {CONF_HOST, CONF_MAC, CONF_HOUSE, CONF_NAME}


def _ago(timestamp: float | None, now: float) -> int | None:
    return None if timestamp is None else round(now - timestamp)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TellStickConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    hub = data.hub
    now = time.time()
    since_seen = hub.seconds_since_seen
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "device": {
            "firmware": hub.firmware,
            "available": hub.available,
            "seconds_since_seen": None if since_seen is None else round(since_seen),
            "unsupported_kinds_heard": hub.unsupported_kinds,
        },
        "sensors": [
            {
                "uid": sensor.uid,
                "protocol": sensor.protocol,
                "model": sensor.model,
                "sensor_id": sensor.sensor_id,
                "quantities": sensor.quantities,
                "first_seen_seconds_ago": _ago(sensor.first_seen, now),
                "last_seen_seconds_ago": _ago(sensor.last_seen, now),
            }
            for sensor in data.sensors
        ],
    }
