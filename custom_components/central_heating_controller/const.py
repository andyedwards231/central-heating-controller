"""Constants for the Central Heating Controller integration."""

DOMAIN = "central_heating_controller"
NAME = "Central Heating Controller"
PLATFORMS = ("switch", "button", "number", "sensor", "select")
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.{{entry_id}}"

CONF_CLIMATE = "climate_entity"
CONF_PERSONS = "person_entities"
CONF_HOME_ZONE = "home_zone_entity"
CONF_SCHEDULE = "schedule_entity"
CONF_DESTINATION = "destination_entity"
CONF_ARRIVAL_TIME = "arrival_time_entity"
CONF_DESTINATION_HOME_VALUE = "destination_home_value"
CONF_ACTIVE_HVAC_MODE = "active_hvac_mode"
CONF_HIGH_TEMP = "high_temperature"
CONF_LOW_TEMP = "low_temperature"
CONF_ECO_TEMP = "eco_temperature"
CONF_FALLBACK_MINUTES = "fallback_warmup_minutes"
CONF_MAX_WARMUP_MINUTES = "maximum_warmup_minutes"
CONF_RESET_LEARNING = "reset_learning"
CONF_SMART_SCHEDULE_ENABLED = "smart_schedule_enabled"
CONF_SMART_COMFORT_SCHEDULE = "smart_comfort_schedule"
CONF_SMART_NIGHT_START = "smart_night_start"
CONF_SMART_NIGHT_END = "smart_night_end"
CONF_SMART_NIGHT_TEMPERATURE = "smart_night_temperature"
CONF_SMART_DAYTIME_DROP = "smart_daytime_drop"

# Internal one-shot marker written by the options flow when the entry is unloaded.
# The coordinator setup added in Task 6 consumes and removes this marker.
OPT_RESET_LEARNING_REQUESTED = "_reset_learning_requested"

DEFAULT_HIGH_C = 20.0
DEFAULT_LOW_C = 17.0
DEFAULT_ECO_C = 14.0
DEFAULT_HIGH_F = 68.0
DEFAULT_LOW_F = 63.0
DEFAULT_ECO_F = 57.0
DEFAULT_FALLBACK_MINUTES = 60
DEFAULT_MAX_WARMUP_MINUTES = 180
DEFAULT_SMART_SCHEDULE_ENABLED = True
DEFAULT_SMART_COMFORT_SCHEDULE = "{}"
DEFAULT_SMART_NIGHT_START = "23:00"
DEFAULT_SMART_NIGHT_END = "07:00"
DEFAULT_SMART_NIGHT_TEMPERATURE_C = 16.0
DEFAULT_SMART_NIGHT_TEMPERATURE_F = 61.0
DEFAULT_SMART_DAYTIME_DROP_C = 2.0
DEFAULT_SMART_DAYTIME_DROP_F = 3.6
HEAT_BLAST_MINUTES = 60

CONF_WEATHER = "weather_entity"
CONF_OUTDOOR_TEMP = "outdoor_temperature_entity"
CONF_HEATING_ACTIVITY = "heating_activity_entity"
CONF_GAS_RATE = "gas_rate_entity"
CONF_GAS_CONSUMPTION = "gas_consumption_entity"
CONF_GAS_PREVIOUS_CONSUMPTION = "gas_previous_consumption_entity"
