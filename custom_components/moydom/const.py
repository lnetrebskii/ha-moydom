"""Constants for the Мой Дом (Электронный город / Дом.ру) integration."""

from __future__ import annotations

DOMAIN = "moydom"

BASE_URL = "https://myhome.proptech.ru"

APP_VERSION = "9.12.0"
APP_VERSION_CODE = 91200000
APP_BRAND = "ntk"
DEVICE_NAME = "Google Pixel 7"
ANDROID_VERSION = "Android 14"
LOGIN_USER_AGENT = "Android"

CONF_PHONE = "phone"
CONF_INSTALLATION_ID = "installation_id"
CONF_ACCESS_TOKEN = "access_token"  # noqa: S105
CONF_REFRESH_TOKEN = "refresh_token"  # noqa: S105
CONF_OPERATOR_ID = "operator_id"
CONF_ACCOUNT_ID = "account_id"
CONF_PLACE_ID = "place_id"

UPDATE_INTERVAL_MINUTES = 10
REQUEST_TIMEOUT_SECONDS = 20
SNAPSHOT_WIDTH = 1280
SNAPSHOT_HEIGHT = 720
OPEN_ACTION_NAME = "accessControlOpen"
