import os

# Telegram Bot Token
TELEGRAM_TOKEN = "8975417358:AAFyBNij4P_zO2BJlsDaIOJdnTJvUbUX8_4"

# Samsara API Token
SAMSARA_API_TOKEN = "samsara_api_A2GbS6AqOKJRxqq3JjOgUg1FirXyYa"

# Apify API Token (fuel prices)
APIFY_API_TOKEN = "apify_api_qcaAze1p0j60GiLck1OV8KxdN922XA1FOoo5"

# Road511 API Key (if needed)
ROAD511_API_KEY = "r511_cbec0e89aae8a87b6779c797be3e3f0f4f5194df2e8b1831f456b88846b1810a"

# OpenRouteService API Key
OPENROUTESERVICE_API_KEY = os.environ.get(
    "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImVlZjk3YjdkNDUxOTQwZjJiZDYzMGVhNzhhMGYwNmY2IiwiaCI6Im11cm11cjY0In0=",
    ""
)

# Supabase/PostgreSQL database connection string
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ==== NEW SETTINGS ====

# Telegram group where all collected data is sent (admin group)
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID", "-5396003539"))  # Your admin group ID

# Dedicated PTI group to receive submitted PTI reports (if different from admin)
PTI_GROUP_ID = int(os.environ.get("PTI_GROUP_ID", "-5396003539"))  # Replace with your PTI group ID

# Dispatcher group for PTI notifications (if different from admin/PTI)
DISPATCHER_GROUP_ID = int(os.environ.get("DISPATCHER_GROUP_ID", "-5396003539"))  # Replace if needed

# Points configuration
POINTS_PER_FUEL_STOP = int(os.environ.get("POINTS_PER_FUEL_STOP", "10"))
POINTS_PER_PTI = int(os.environ.get("POINTS_PER_PTI", "20"))

# Donation info (crypto wallet or other)
DONATION_WALLET = os.environ.get("DONATION_WALLET", "your_wallet_address")

# Web app URL (must be HTTPS)
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://samsara-bot-test.onrender.com")

# Minimum points to convert to money (monthly)
MIN_POINTS_FOR_CASHOUT = int(os.environ.get("MIN_POINTS_FOR_CASHOUT", "100"))
