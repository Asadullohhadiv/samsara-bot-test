import os

# Secrets MUST be supplied by Render/Supabase environment variables.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
SAMSARA_API_TOKEN = os.environ.get("SAMSARA_API_TOKEN", "")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
ROAD511_API_KEY = os.environ.get("ROAD511_API_KEY", "")
OPENROUTESERVICE_API_KEY = os.environ.get("OPENROUTESERVICE_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID", "0"))
PTI_GROUP_ID = int(os.environ.get("PTI_GROUP_ID", str(ADMIN_GROUP_ID)))
DISPATCHER_GROUP_ID = int(os.environ.get("DISPATCHER_GROUP_ID", str(ADMIN_GROUP_ID)))

POINTS_PER_FUEL_STOP = int(os.environ.get("POINTS_PER_FUEL_STOP", "10"))
POINTS_PER_PTI = int(os.environ.get("POINTS_PER_PTI", "20"))
DONATION_WALLET = os.environ.get("DONATION_WALLET", "")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "")
MIN_POINTS_FOR_CASHOUT = int(os.environ.get("MIN_POINTS_FOR_CASHOUT", "100"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_API_SECRET = os.environ.get("ADMIN_API_SECRET", "")
DRIVER_API_SECRET = os.environ.get("DRIVER_API_SECRET", ADMIN_API_SECRET)

# Fuel tuning knobs.
FUEL_ROUTE_BUFFER_MILES = float(os.environ.get("FUEL_ROUTE_BUFFER_MILES", "8"))
FUEL_MIN_RESERVE_PERCENT = float(os.environ.get("FUEL_MIN_RESERVE_PERCENT", "12"))
FUEL_MAX_SEARCH_MILES = float(os.environ.get("FUEL_MAX_SEARCH_MILES", "220"))


def validate_required_secrets():
    required = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "DATABASE_URL": DATABASE_URL,
        "SAMSARA_API_TOKEN": SAMSARA_API_TOKEN,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
