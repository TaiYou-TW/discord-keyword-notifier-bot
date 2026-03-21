import logging
import os

from dotenv import load_dotenv

load_dotenv()

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
)
logger = logging.getLogger(__name__)

# Add file handler for ERROR logs
error_log_path = os.getenv("ERROR_LOG_PATH", "error.log")
file_handler = logging.FileHandler(error_log_path, encoding="utf-8")
file_handler.setLevel(logging.ERROR)
file_formatter = logging.Formatter(LOG_FORMAT)
file_handler.setFormatter(file_formatter)
logging.getLogger().addHandler(file_handler)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Please set it in your environment or in a .env file."
    )

DB_PATH = os.getenv("DB_PATH", "keywords.db")
DEFAULT_COOLDOWN = int(os.getenv("DEFAULT_COOLDOWN", "30"))
NOTIFICATION_MAX_DESCRIPTION_LENGTH = int(
    os.getenv("NOTIFICATION_MAX_DESCRIPTION_LENGTH", "150")
)
MENTIONED_EMOJI = os.getenv("MENTIONED_EMOJI", "<:mcc_hoeh:1484208659658576143>")
MENTIONED_EMOJI2 = os.getenv("MENTIONED_EMOJI2", "<:fww_hoeh:1484923834279788655>")

HOLODEX_API_KEY = os.getenv("HOLODEX_API_KEY", "")
HOLODEX_ORG = os.getenv("HOLODEX_ORG", "")
HOLODEX_CHANNEL_IDS = [
    c.strip() for c in os.getenv("HOLODEX_CHANNEL_IDS", "").split(",") if c.strip()
]
HOLODEX_NOTIFY_LIVE_CHANNEL_ID = os.getenv("HOLODEX_NOTIFY_LIVE_CHANNEL_ID")
HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID = os.getenv("HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID")
HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID = os.getenv("HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID")

HOLODEX_NOTIFY_LIVE_CHANNEL_ID = (
    int(HOLODEX_NOTIFY_LIVE_CHANNEL_ID) if HOLODEX_NOTIFY_LIVE_CHANNEL_ID else None
)
HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID = (
    int(HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID)
    if HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID
    else None
)
HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID = (
    int(HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID) if HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID else None
)

HOLODEX_POLL_INTERVAL = int(os.getenv("HOLODEX_POLL_INTERVAL", "60"))
HOLODEX_MEMORY_LIMIT = int(os.getenv("HOLODEX_MEMORY_LIMIT", "2000"))

ZERO_WIDTH_SPACE = "\u200b"
