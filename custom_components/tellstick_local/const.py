"""Constants for the TellStick Net (local) integration."""

DOMAIN = "tellstick_local"

CONF_MAC = "mac"
CONF_SWITCHES = "switches"
CONF_HOUSE = "house"
CONF_UNIT = "unit"
# Kind of self-learning receiver; entries without it are switches
CONF_TYPE = "type"
TYPE_SWITCH = "switch"
TYPE_DIMMER = "dimmer"
# Self-learning transmitters (they send the same frames as a remote)
TYPE_DOOR = "door"
TYPE_MOTION = "motion"
TYPE_BUTTON = "button"
# Motion sensors that only send "on" are turned off after this many seconds
MOTION_OFF_AFTER = 60
CONF_NEW_SENSORS_ENABLED = "new_sensors_enabled"
# Sensor quantity shown as a battery binary sensor, not a sensor
QUANTITY_BATTERY_LOW = "battery_low"

DEFAULT_NEW_SENSORS_ENABLED = True

# Arctech/Nexa self-learning address space
HOUSE_MIN = 1
HOUSE_MAX = 67108863  # 26 bits
UNIT_MIN = 1
UNIT_MAX = 16

# Fired for every decoded RF command (remote buttons, wall switches, ...)
EVENT_RF_COMMAND = f"{DOMAIN}_command"

SIGNAL_PACKET = f"{DOMAIN}_packet_{{entry_id}}"
SIGNAL_AVAILABLE = f"{DOMAIN}_available_{{entry_id}}"

STORAGE_VERSION = 1
# {"sensors": [KnownSensor.to_dict(), ...]}, see known_sensors.py
STORAGE_KEY = f"{DOMAIN}.sensors.{{entry_id}}"

# Last stable TellStick Net firmware, and the first with the local interface
# (reglistener/setip/saveip are absent from fw2-4 and betas 12/16). Older
# versions get a repair issue.
LATEST_FIRMWARE = 17
FIRMWARE_UPDATE_URL = "https://developer.telldus.com/wiki/Firmware_linux"
ISSUE_OLD_FIRMWARE = "old_firmware_{entry_id}"


def selflearning_unique_id(mac: str, house: int, unit: int) -> str:
    """Unique id (and device identifier) of a configured switch or dimmer."""
    return f"{selflearning_prefix(mac)}{house}-{unit}"


def selflearning_prefix(mac: str) -> str:
    return f"{mac}-arctech-selflearning-"
