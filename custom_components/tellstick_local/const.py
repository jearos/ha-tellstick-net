"""Constants for the TellStick Net (local) integration."""

DOMAIN = "tellstick_local"

CONF_MAC = "mac"
CONF_SWITCHES = "switches"
CONF_HOUSE = "house"
CONF_UNIT = "unit"
CONF_NEW_SENSORS_ENABLED = "new_sensors_enabled"

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
