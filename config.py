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
ADMIN_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ADMIN_USER_IDS", "").split(",")
    if uid.strip()
]
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

TWITTER_SCREEN_NAMES = [
    n.strip() for n in os.getenv("TWITTER_SCREEN_NAMES", "").split(",") if n.strip()
]
TWITTER_NOTIFY_CHANNEL_ID = os.getenv("TWITTER_NOTIFY_CHANNEL_ID")
TWITTER_NOTIFY_CHANNEL_ID = (
    int(TWITTER_NOTIFY_CHANNEL_ID) if TWITTER_NOTIFY_CHANNEL_ID else None
)
TWITTER_POLL_INTERVAL = int(os.getenv("TWITTER_POLL_INTERVAL", "60"))
TWITTER_WORKER_COUNT = int(os.getenv("TWITTER_WORKER_COUNT", "4"))
TWITTER_WAIT_BETWEEN_PROFILES = int(os.getenv("TWITTER_WAIT_BETWEEN_PROFILES", "3"))
TWITTER_WORKER_START_DELAY = int(os.getenv("TWITTER_WORKER_START_DELAY", "2"))
TWITTER_RATE_LIMIT_RESERVE = int(os.getenv("TWITTER_RATE_LIMIT_RESERVE", "2"))
TWITTER_MEMORY_LIMIT = int(os.getenv("TWITTER_MEMORY_LIMIT", "2000"))
TWITTER_SYNDICATION_USER_AGENT = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)",
]

ZERO_WIDTH_SPACE = "\u200b"
