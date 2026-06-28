"""Constants for the Central Heating Controller integration."""

DOMAIN = "central_heating_controller"
NAME = "Central Heating Controller"
PLATFORMS = ("switch", "button", "number", "sensor")
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

DEFAULT_HIGH_C = 20.0
DEFAULT_LOW_C = 17.0
DEFAULT_ECO_C = 14.0
DEFAULT_HIGH_F = 68.0
DEFAULT_LOW_F = 63.0
DEFAULT_ECO_F = 57.0
DEFAULT_FALLBACK_MINUTES = 60
DEFAULT_MAX_WARMUP_MINUTES = 180
HEAT_BLAST_MINUTES = 60
