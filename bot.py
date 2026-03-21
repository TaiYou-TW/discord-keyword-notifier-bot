import sqlite3
import random
import discord
from discord import app_commands

from config import DB_PATH, logger, MENTIONED_EMOJI, MENTIONED_EMOJI2
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

    async def reply_when_mentioned(self, message: discord.Message) -> None:
        # reply emoji2 10% of the time, emoji1 90% of the time
        if random.random() < 0.1:
            await message.reply(MENTIONED_EMOJI2)
        else:
            await message.reply(MENTIONED_EMOJI)


bot = MyBot()
