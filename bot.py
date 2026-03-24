import sqlite3
import random
import asyncio
from collections import Counter
import discord
from discord import app_commands

from config import DB_PATH, logger, MENTIONED_EMOJI, MENTIONED_EMOJI2, ADMIN_USER_IDS
from holodex import HolodexMixin
from keyword_mixin import KeywordMixin
from twitter_syndication import TwitterSyndicationMixin


class MyBot(TwitterSyndicationMixin, HolodexMixin, KeywordMixin, discord.Client):
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
        self.twitter_profile_notified = {}
        self.twitter_monitor_task = None
        self.guild_member_ids = {}  # { guild_id: set(user_id) }

        # In-memory dedupe for keyword notification (message_id:keyword)
        self.notified_message_keywords = (
            set()
        )  # Set[str], key = f"{message_id}:{keyword}"

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
            "CREATE TABLE IF NOT EXISTS twitter_profile_notified (screen_name TEXT, tweet_id TEXT, PRIMARY KEY (screen_name, tweet_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS emoji_usage (user_id INTEGER, emoji TEXT, count INTEGER DEFAULT 1, last_used INTEGER, PRIMARY KEY (user_id, emoji))"
        )
        conn.commit()
        conn.close()

        logger.info("Database setup complete.")

        self.load_data()
        self.load_twitter_profile_data()

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

    def _record_emoji_usage_sync(self, user_id: int, emoji: str) -> None:
        """Synchronous helper for emoji usage update, safe inside executor."""
        import time
        current_time = int(time.time())
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO emoji_usage (user_id, emoji, count, last_used)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id, emoji) DO UPDATE SET 
                count = count + 1,
                last_used = excluded.last_used
            """,
            (user_id, emoji, current_time),
        )
        conn.commit()
        conn.close()

    async def record_emoji_usage(self, user_id: int, emoji: str) -> None:
        """Record emoji usage asynchronously via thread executor."""
        await asyncio.to_thread(self._record_emoji_usage_sync, user_id, emoji)

    async def scan_channel_history(self, channel: discord.TextChannel, limit: int = 1000) -> tuple[int, int]:
        """Scan channel history for emoji usage statistics"""
        import re
        
        messages_scanned = 0
        emojis_found = 0
        
        # Match Discord custom emojis (both static and animated)
        custom_emoji_pattern = r'<a?:\w+:\d+>'
        # For Unicode emojis
        unicode_emoji_pattern = r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002500-\U00002BEF\U00002702-\U000027B0\U00002702-\U000027B0\U000024C2-\U0001F251\U0001f926-\U0001f937\U00010000-\U0010ffff\U0001f1e6-\U0001f1ff]'
        
        try:
            # If limit is None, use None to get all messages (no limit)
            local_counter = Counter()
            async for message in channel.history(limit=limit):
                if message.author.bot:
                    continue

                messages_scanned += 1

                custom_emojis = re.findall(custom_emoji_pattern, message.content)
                unicode_emojis = re.findall(unicode_emoji_pattern, message.content)
                for emoji in custom_emojis + unicode_emojis:
                    local_counter[(message.author.id, emoji)] += 1
                    emojis_found += 1

                # yield control to event loop regularly
                if messages_scanned % 100 == 0:
                    await asyncio.sleep(0)

                # flush in batches to avoid huge memory usage
                if len(local_counter) > 5000:
                    await asyncio.to_thread(self._batch_record_emoji_usage_sync, local_counter)
                    local_counter.clear()

            if local_counter:
                await asyncio.to_thread(self._batch_record_emoji_usage_sync, local_counter)

        except discord.Forbidden:
            logger.warning(f"Cannot access history for channel {channel.name} ({channel.id})")
        except Exception as e:
            logger.exception(f"Error scanning channel {channel.name} ({channel.id}): {e}")
            
        return messages_scanned, emojis_found

    async def scan_guild_history(self, guild: discord.Guild, limit_per_channel: int = 1000, unlimited: bool = False) -> tuple[int, int, int]:
        """Scan all text channels in a guild for emoji usage statistics"""
        total_messages = 0
        total_emojis = 0
        channels_scanned = 0
        
        # Get all text channels that the bot can read
        text_channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]
        text_channels = [ch for ch in text_channels if ch.permissions_for(guild.me).read_message_history]
        
        # If unlimited is True, set limit to None (no limit)
        actual_limit = None if unlimited else limit_per_channel
        
        logger.info(f"Starting guild scan for {guild.name} ({guild.id}): {len(text_channels)} channels to scan, limit_per_channel={'unlimited' if unlimited else limit_per_channel}")
        
        for channel in text_channels:
            try:
                logger.debug(f"Scanning channel {channel.name} ({channel.id})")
                messages, emojis = await self.scan_channel_history(channel, actual_limit)
                total_messages += messages
                total_emojis += emojis
                channels_scanned += 1
                
                logger.debug(f"Channel {channel.name}: {messages} messages, {emojis} emojis")
                
            except Exception as e:
                logger.exception(f"Error scanning channel {channel.name} ({channel.id}): {e}")
                continue
                
        logger.info(f"Guild scan completed for {guild.name}: {channels_scanned} channels, {total_messages} messages, {total_emojis} emojis")
        return total_messages, total_emojis, channels_scanned

    def _batch_record_emoji_usage_sync(self, counter: Counter) -> None:
        """Batch commit emoji counts to SQLite synchronously."""
        import time
        conn = sqlite3.connect(self.db_path)
        now = int(time.time())

        # one SQL statement per unique key for simplicity
        for (user_id, emoji), delta in counter.items():
            conn.execute(
                """
                INSERT INTO emoji_usage (user_id, emoji, count, last_used)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, emoji) DO UPDATE SET
                    count = count + ?,
                    last_used = ?
                """,
                (user_id, emoji, delta, now, delta, now),
            )

        conn.commit()
        conn.close()

    async def reply_when_mentioned(self, message: discord.Message) -> None:
        # cool feature for admins only
        if message.author.id in ADMIN_USER_IDS:
            if message.mentions > 1:
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
