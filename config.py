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

HOLODEX_BASE_URL = os.getenv("HOLODEX_BASE_URL", "https://holodex.net/api/v2")
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

YT_API_BASE_URL = os.getenv("YT_API_BASE_URL", "http://127.0.0.1:8080")
YT_CHANNEL_IDS = [
    c.strip() for c in os.getenv("YT_CHANNEL_IDS", "").split(",") if c.strip()
]
YT_NOTIFY_CHANNEL_ID = os.getenv("YT_NOTIFY_CHANNEL_ID")
YT_NOTIFY_CHANNEL_ID = int(YT_NOTIFY_CHANNEL_ID) if YT_NOTIFY_CHANNEL_ID else None
YT_POLL_INTERVAL = int(os.getenv("YT_POLL_INTERVAL", "60"))
YT_MEMORY_LIMIT = int(os.getenv("YT_MEMORY_LIMIT", "2000"))

# YouTube membership verification via Google OAuth
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
# force-ssl is required to read members-only comment threads. youtube.readonly
# returns HTTP 403 insufficientPermissions for members-only content, so it
# can't verify membership. Overridable, but the default must be force-ssl.
MEMBERSHIP_OAUTH_SCOPE = os.getenv(
    "MEMBERSHIP_OAUTH_SCOPE", "https://www.googleapis.com/auth/youtube.force-ssl"
)
MEMBERSHIP_OAUTH_HOST = os.getenv("MEMBERSHIP_OAUTH_HOST", "0.0.0.0")
MEMBERSHIP_OAUTH_PORT = int(os.getenv("MEMBERSHIP_OAUTH_PORT", "8081"))

# Optional / legacy: only used to backfill the guild when migrating an older
# single-guild membership_channels table. The bot serves any guild it's in;
# mappings carry their own guild id (set when an admin runs /membership_add).
MEMBERSHIP_GUILD_ID = os.getenv("MEMBERSHIP_GUILD_ID")
MEMBERSHIP_GUILD_ID = int(MEMBERSHIP_GUILD_ID) if MEMBERSHIP_GUILD_ID else None
# Channel -> role mappings are managed at runtime by admins via the
# /membership_add, /membership_remove and /membership_list commands and stored
# in the database (table membership_channels), not in env. Members-only probe
# videos are always auto-discovered from each channel's UUMO uploads playlist.

# Optional YouTube Data API key used to list the members-only playlist without
# depending on a member's token. Recommended for reliable auto-discovery.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# Fernet key (base64, 32 bytes) to encrypt stored refresh tokens at rest.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MEMBERSHIP_TOKEN_ENC_KEY = os.getenv("MEMBERSHIP_TOKEN_ENC_KEY", "")
# How often (seconds) the monitor wakes to re-verify members. Each wake only
# re-checks members whose last check is older than MEMBERSHIP_RECHECK_MIN_INTERVAL,
# so a shorter wake mainly means newly-linked channels get picked up sooner.
MEMBERSHIP_CHECK_INTERVAL = int(os.getenv("MEMBERSHIP_CHECK_INTERVAL", "21600"))
# Minimum time (seconds) between re-checks of the same member. YouTube memberships
# renew monthly, so ~once/day is plenty and keeps quota usage low. Default 20h.
MEMBERSHIP_RECHECK_MIN_INTERVAL = int(
    os.getenv("MEMBERSHIP_RECHECK_MIN_INTERVAL", "72000")
)
# How many members-only videos to probe per channel before deciding. Members
# pass on the first probe; extra probes only cost quota on non-members, so keep
# this at 1 unless a channel's latest members-only upload often has comments off.
MEMBERSHIP_MAX_PROBE_VIDEOS = int(os.getenv("MEMBERSHIP_MAX_PROBE_VIDEOS", "1"))
# Soft daily cap (units) for membership API calls. The YouTube Data API default
# project quota is 10k/day; the monitor stops re-checking once this is reached
# and resumes after the daily reset, so it degrades gracefully instead of erroring.
MEMBERSHIP_DAILY_QUOTA = int(os.getenv("MEMBERSHIP_DAILY_QUOTA", "9000"))
# How long (seconds) a channel's discovered members-only probe videos stay fresh
# in the DB before re-listing them (they rarely change). Default 24h.
MEMBERSHIP_PROBE_TTL = int(os.getenv("MEMBERSHIP_PROBE_TTL", "86400"))
# Optional URL to redirect the browser to after the OAuth callback completes.
MEMBERSHIP_SUCCESS_REDIRECT = os.getenv("MEMBERSHIP_SUCCESS_REDIRECT", "")

# YouTube live-stream recording (admin /record_* commands). Requires yt-dlp
# (in requirements.txt) and ffmpeg (installed in the image) to mux live streams.
# Recordings are written to RECORDING_OUTPUT_DIR; with the default compose
# bind-mount this lands inside the repo directory on the host. yt-dlp is run as
# `python -m yt_dlp` unless YT_DLP_PATH points at a binary.
RECORDING_OUTPUT_DIR = os.getenv("RECORDING_OUTPUT_DIR", "recordings")
RECORDING_MAX_CONCURRENT = int(os.getenv("RECORDING_MAX_CONCURRENT", "3"))
YT_DLP_PATH = os.getenv("YT_DLP_PATH", "")
# Auto-delete recordings older than this many days (disk cleanup). 0 disables.
RECORDING_RETENTION_DAYS = int(os.getenv("RECORDING_RETENTION_DAYS", "7"))
RECORDING_COOKIE_FILE = os.getenv("RECORDING_COOKIE_FILE", "")

# Optional cloud upload for finished recordings via rclone (`/record_upload`),
# for files too large for a direct Discord upload. RCLONE_REMOTE is an rclone
# destination such as "gdrive:vtuber-recordings" (leave blank to disable);
# RCLONE_CONFIG optionally points at an rclone.conf; RCLONE_PATH overrides the
# binary. rclone is used rather than rsync because it speaks Google Drive (and
# other cloud backends) directly and can hand back a shareable link.
RCLONE_REMOTE = os.getenv("RCLONE_REMOTE", "")
RCLONE_CONFIG = os.getenv("RCLONE_CONFIG", "")
RCLONE_PATH = os.getenv("RCLONE_PATH", "rclone")

ZERO_WIDTH_SPACE = "\u200b"
