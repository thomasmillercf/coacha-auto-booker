from datetime import timedelta

DOMAIN = "coacha"

BASE_URL = "https://my.coacha.app"
OIDC_CLIENT_ID = "CoachaApp"
OIDC_SCOPE = "CoachaAppAPI openid profile"
OIDC_REDIRECT_URI = f"{BASE_URL}/authentication/login-callback"

CONF_LOGIN_URL = "login_url"
CONF_SESSION_TYPES = "session_types"
CONF_WAITING_LIST = "waiting_list"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_WEEKDAYS = "weekdays"

DEFAULT_LOGIN_URL = f"{BASE_URL}/login/WGCSKIRACECLUBLOGIN"
DEFAULT_SCAN_INTERVAL_SECONDS = 300
MINIMUM_SCAN_INTERVAL_SECONDS = 15
WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DEFAULT_WEEKDAYS = ["friday"]

LOOKAHEAD = timedelta(days=90)

EVENT_BOOKED = f"{DOMAIN}_booked"
EVENT_SESSION_AVAILABLE = f"{DOMAIN}_session_available"
