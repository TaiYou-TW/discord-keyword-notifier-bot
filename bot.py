import sqlite3
import random
import asyncio
import re
from collections import Counter
import discord
from discord import app_commands
import emoji

from config import DB_PATH, logger, MENTIONED_EMOJI, MENTIONED_EMOJI2, ADMIN_USER_IDS
from holodex import HolodexMixin
from keyword_mixin import KeywordMixin
from membership import MembershipMixin
from recording import RecordingMixin
from twitter_syndication import TwitterSyndicationMixin
from youtube_community import YouTubeCommunityMixin
from enums import HolodexNotifyType


# Match Discord custom emojis (both static and animated): <:name:id> / <a:name:id>
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:\w+:\d+>")

# emoji_usage tracks counts per (user, emoji, recipient, server):
#   user_id          - who used the emoji (message author or reactor)
#   received_user_id - reaction recipient (the reacted message's author);
#                      0 for message content, which has no recipient
#   server_id        - guild id; 0 for DMs / no guild
EMOJI_USAGE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS emoji_usage (
        user_id INTEGER NOT NULL,
        emoji TEXT NOT NULL,
        received_user_id INTEGER NOT NULL DEFAULT 0,
        server_id INTEGER NOT NULL DEFAULT 0,
        count INTEGER NOT NULL DEFAULT 1,
        last_used INTEGER,
        PRIMARY KEY (user_id, emoji, received_user_id, server_id)
    )
"""


def extract_emojis(text: str) -> list[str]:
    """Return every emoji in ``text`` (custom Discord + Unicode), preserving repeats."""
    if not text:
        return []
    found = CUSTOM_EMOJI_PATTERN.findall(text)
    found.extend(item["emoji"] for item in emoji.emoji_list(text))
    return found


class MyBot(
    YouTubeCommunityMixin,
    TwitterSyndicationMixin,
    HolodexMixin,
    KeywordMixin,
    MembershipMixin,
    RecordingMixin,
    discord.Client,
):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db_path = DB_PATH

        self.keyword_cache = {}  # { user_id: [kw1, kw2] }
        self.cooldown_settings = {}  # { user_id: seconds }
        self.last_notified = {}  # { (user_id, kw): timestamp }

        # Keep a bounded history of notified IDs to avoid duplicate alerts.
        # { source_key: { stream_or_video_id: None } }
        self.holodex_notified_live = {}
        self.holodex_notified_upcoming = {}
        self.holodex_notified_upload = {}
        self.holodex_status_messages = {
            HolodexNotifyType.LIVE: {},
            HolodexNotifyType.UPCOMING: {},
        }
        self.holodex_monitor_task = None
        self.twitter_profile_notified = {}
        self.twitter_monitor_task = None
        self.yt_community_notified = {}
        self.yt_community_monitor_task = None
        self.guild_member_ids = {}  # { guild_id: set(user_id) }
        self.muted_channel_ids = {}  # { user_id: set(channel_id) }

        # YouTube membership verification (see MembershipMixin)
        self.membership_monitor_task = None
        self._membership_runner = None
        self._fernet_cache = False  # sentinel: cipher not built yet
        self._probe_cache = {}  # { yt_channel_id: {"ids": [...], "ts": int} }
        # [ (guild_id, yt_channel_id, role_id), ... ]
        self.membership_channel_map = []

        # YouTube live-stream recording (see RecordingMixin)
        self.active_recordings = {}  # { key: {process, log_fh, started_at, ...} }
        self._recording_available_cache = None  # yt-dlp availability (cached)
        self.recording_cleanup_task = None  # retention sweep task

        # In-memory dedupe for keyword notification (message_id:keyword)
        self.notified_message_keywords = (
            set()
        )  # Set[str], key = f"{message_id}:{keyword}"

    def _ensure_emoji_usage_schema(self, conn: sqlite3.Connection) -> None:
        """Create emoji_usage, migrating the older (user_id, emoji) schema.

        Older rows predate the received_user_id/server_id columns, so they are
        copied in with both set to 0 (treated as message usage in no guild).
        """
        cols = [
            row[1] for row in conn.execute("PRAGMA table_info(emoji_usage)").fetchall()
        ]
        if cols and ("received_user_id" not in cols or "server_id" not in cols):
            logger.info(
                "Migrating emoji_usage to new schema (received_user_id, server_id)..."
            )
            conn.execute("ALTER TABLE emoji_usage RENAME TO emoji_usage_legacy")
            conn.execute(EMOJI_USAGE_SCHEMA)
            conn.execute(
                """
                INSERT INTO emoji_usage
                    (user_id, emoji, received_user_id, server_id, count, last_used)
                SELECT user_id, emoji, 0, 0, count, last_used FROM emoji_usage_legacy
                """
            )
            conn.execute("DROP TABLE emoji_usage_legacy")
            logger.info("emoji_usage migration complete.")
        else:
            conn.execute(EMOJI_USAGE_SCHEMA)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_emoji_usage_server_received "
            "ON emoji_usage(server_id, received_user_id)"
        )

    async def setup_hook(self):
        logger.info("Setting up database...")

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_keywords (user_id INTEGER, keyword TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, seconds INTEGER, permission_verified INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS holodex_notified (source_key TEXT, item_id TEXT, notify_type TEXT, PRIMARY KEY (source_key, item_id, notify_type))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS holodex_status_messages (source_key TEXT, stream_id TEXT, notify_type TEXT, channel_id INTEGER, message_id INTEGER, PRIMARY KEY (stream_id, notify_type))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS twitter_profile_notified (screen_name TEXT, tweet_id TEXT, PRIMARY KEY (screen_name, tweet_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS yt_community_notified (source_key TEXT, post_id TEXT, PRIMARY KEY (source_key, post_id))"
        )
        self._ensure_emoji_usage_schema(conn)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS muted_channels (user_id INTEGER, channel_id INTEGER, PRIMARY KEY (user_id, channel_id))"
        )
        self.ensure_membership_schema(conn)
        conn.commit()
        conn.close()

        logger.info("Database setup complete.")

        self.load_data()
        self.load_holodex_status_messages()
        self.load_muted_channels()
        self.load_twitter_profile_data()
        self.load_youtube_community_data()
        self.load_membership_channels()

        await self.tree.sync()

    async def cache_guild_members(self, guild: discord.Guild) -> None:
        if not self.intents.members:
            logger.warning(
                "Members intent is disabled; cannot warm member cache for guild %s",
                guild.id,
            )
            return

        try:
            if not guild.chunked:
                await guild.chunk(cache=True)

            self.guild_member_ids[guild.id] = {member.id for member in guild.members}
            logger.info(
                "Cached %d members for guild %s",
                len(self.guild_member_ids[guild.id]),
                guild.id,
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to cache members for guild %s", guild.id)

    async def warm_member_cache(self) -> None:
        for guild in self.guilds:
            await self.cache_guild_members(guild)

    async def can_send_permission_test_message(
        self, interaction: discord.Interaction
    ) -> bool:
        try:
            embed = discord.Embed(
                title="✅ 權限測試",
                description="恭喜！Bot 成功發送訊息到你的 DM。你已經可以接收關鍵字通知了。",
                color=0x2ECC71,
            )
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            try:
                await interaction.followup.send(
                    "❌ 無法發送 DM 訊息！\n請檢查以下設定：\n"
                    "1. 確認你的 DM 是開放的（設定 > 內容與社交 > 社交權限 > 私人訊息）\n"
                    "2. 檢查是否有封鎖 Bot\n\n"
                    "請先完成上述設定後再試一次。",
                    ephemeral=True,
                )
                logger.warning(
                    "Failed to send test message to user %s(%d): Permission denied",
                    interaction.user,
                    interaction.user.id,
                )
            except Exception as e:
                logger.exception(
                    "Error sending DM permission warning to user %s(%d): %s",
                    interaction.user,
                    interaction.user.id,
                    e,
                )
            return False
        except Exception as e:
            try:
                await interaction.followup.send(
                    f"⚠️ 發送測試訊息時出錯：{str(e)}", ephemeral=True
                )
                logger.exception(
                    "Error sending test message to user %s(%d): %s",
                    interaction.user,
                    interaction.user.id,
                    e,
                )
            except Exception as e2:
                logger.exception(
                    "Error sending error message to user %s(%d): %s",
                    interaction.user,
                    interaction.user.id,
                    e2,
                )
            return False
        return True

    def has_permission_verified(self, uid: int) -> bool:
        # Check if user has already verified permissions
        conn = sqlite3.connect(self.db_path)
        result = conn.execute(
            "SELECT permission_verified FROM user_settings WHERE user_id = ?", (uid,)
        ).fetchone()
        conn.close()
        return result[0] if result else 0

    def _record_emoji_usage_sync(
        self,
        user_id: int,
        emoji: str,
        received_user_id: int = 0,
        server_id: int = 0,
    ) -> None:
        """Synchronous helper for emoji usage update, safe inside executor."""
        import time

        current_time = int(time.time())
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO emoji_usage (user_id, emoji, received_user_id, server_id, count, last_used)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id, emoji, received_user_id, server_id) DO UPDATE SET
                count = count + 1,
                last_used = excluded.last_used
            """,
            (user_id, emoji, received_user_id, server_id, current_time),
        )
        conn.commit()
        conn.close()

    async def record_emoji_usage(
        self,
        user_id: int,
        emoji: str,
        received_user_id: int = 0,
        server_id: int = 0,
    ) -> None:
        """Record a single emoji use asynchronously via thread executor.

        Used for reactions: ``user_id`` reacted to ``received_user_id``'s
        message in ``server_id`` with ``emoji``. Errors are logged and
        swallowed so a DB hiccup never bubbles up into the event handler.
        """
        try:
            await asyncio.to_thread(
                self._record_emoji_usage_sync,
                user_id,
                emoji,
                received_user_id,
                server_id,
            )
        except Exception:
            logger.exception("Failed to record emoji usage for user %d", user_id)

    async def record_message_emojis(self, message: discord.Message) -> None:
        """Record every emoji contained in a message's content for its author."""
        if message.author.bot:
            return
        emojis = extract_emojis(message.content)
        if not emojis:
            return
        # Message content has no reaction recipient -> received_user_id = 0.
        server_id = message.guild.id if message.guild else 0
        counter = Counter((message.author.id, e, 0, server_id) for e in emojis)
        try:
            await asyncio.to_thread(self._batch_record_emoji_usage_sync, counter)
        except Exception:
            logger.exception(
                "Failed to record message emojis for user %d (message %d)",
                message.author.id,
                message.id,
            )

    async def scan_channel_history(
        self, channel: discord.TextChannel, limit: int = 1000
    ) -> tuple[int, int]:
        """Scan channel history for emoji usage statistics"""
        messages_scanned = 0
        emojis_found = 0
        server_id = channel.guild.id if channel.guild else 0

        try:
            # If limit is None, use None to get all messages (no limit)
            local_counter = Counter()
            async for message in channel.history(limit=limit):
                if message.author.bot:
                    continue

                messages_scanned += 1

                # History scan covers message content only (no recipient).
                for found in extract_emojis(message.content):
                    local_counter[(message.author.id, found, 0, server_id)] += 1
                    emojis_found += 1

                # yield control to event loop regularly
                if messages_scanned % 100 == 0:
                    await asyncio.sleep(0)

                # flush in batches to avoid huge memory usage
                if len(local_counter) > 5000:
                    await asyncio.to_thread(
                        self._batch_record_emoji_usage_sync, local_counter
                    )
                    local_counter.clear()

            if local_counter:
                await asyncio.to_thread(
                    self._batch_record_emoji_usage_sync, local_counter
                )

        except discord.Forbidden:
            logger.warning(
                f"Cannot access history for channel {channel.name} ({channel.id})"
            )
        except Exception as e:
            logger.exception(
                f"Error scanning channel {channel.name} ({channel.id}): {e}"
            )

        return messages_scanned, emojis_found

    async def scan_guild_history(
        self,
        guild: discord.Guild,
        limit_per_channel: int = 1000,
        unlimited: bool = False,
    ) -> tuple[int, int, int]:
        """Scan all text channels in a guild for emoji usage statistics"""
        total_messages = 0
        total_emojis = 0
        channels_scanned = 0

        # Get all text channels that the bot can read
        text_channels = [
            ch for ch in guild.channels if isinstance(ch, discord.TextChannel)
        ]
        text_channels = [
            ch
            for ch in text_channels
            if ch.permissions_for(guild.me).read_message_history
        ]

        # If unlimited is True, set limit to None (no limit)
        actual_limit = None if unlimited else limit_per_channel

        logger.info(
            f"Starting guild scan for {guild.name} ({guild.id}): {len(text_channels)} channels to scan, limit_per_channel={'unlimited' if unlimited else limit_per_channel}"
        )

        for channel in text_channels:
            try:
                logger.debug(f"Scanning channel {channel.name} ({channel.id})")
                messages, emojis = await self.scan_channel_history(
                    channel, actual_limit
                )
                total_messages += messages
                total_emojis += emojis
                channels_scanned += 1

                logger.debug(
                    f"Channel {channel.name}: {messages} messages, {emojis} emojis"
                )

            except Exception as e:
                logger.exception(
                    f"Error scanning channel {channel.name} ({channel.id}): {e}"
                )
                continue

        logger.info(
            f"Guild scan completed for {guild.name}: {channels_scanned} channels, {total_messages} messages, {total_emojis} emojis"
        )
        return total_messages, total_emojis, channels_scanned

    def _batch_record_emoji_usage_sync(self, counter: Counter) -> None:
        """Batch commit emoji counts to SQLite synchronously."""
        import time

        conn = sqlite3.connect(self.db_path)
        now = int(time.time())

        # one SQL statement per unique key for simplicity
        for (user_id, emoji, received_user_id, server_id), delta in counter.items():
            conn.execute(
                """
                INSERT INTO emoji_usage (user_id, emoji, received_user_id, server_id, count, last_used)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, emoji, received_user_id, server_id) DO UPDATE SET
                    count = count + ?,
                    last_used = ?
                """,
                (user_id, emoji, received_user_id, server_id, delta, now, delta, now),
            )

        conn.commit()
        conn.close()

    async def reply_when_mentioned(self, message: discord.Message) -> None:
        # cool feature for admins only
        if message.author.id in ADMIN_USER_IDS:
            if len(message.mentions) > 1:
                for user in message.mentions:
                    if user.id == self.user.id:
                        continue
                    await message.reply(
                        f"{user.mention} {MENTIONED_EMOJI}", mention_author=False
                    )
                return

        # reply emoji2 10% of the time, emoji1 90% of the time
        if random.random() < 0.1:
            await message.reply(MENTIONED_EMOJI2)
        else:
            await message.reply(MENTIONED_EMOJI)


bot = MyBot()
