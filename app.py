import logging
import os
import re

from dotenv import load_dotenv
import discord
from discord import app_commands
import sqlite3
import time

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Please set it in your environment or in a .env file."
    )

DB_PATH = os.getenv("DB_PATH", "keywords.db")
DEFAULT_COOLDOWN = int(os.getenv("DEFAULT_COOLDOWN", "30"))


class MyBot(discord.Client):
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
        self.guild_member_ids = {}  # { guild_id: set(user_id) }

    def load_data(self):
        logger.info("Loading data from database...")

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        res = c.execute("SELECT user_id, keyword FROM user_keywords")
        all_keywords = res.fetchall()
        for uid, kw in all_keywords:
            if uid not in self.keyword_cache:
                self.keyword_cache[uid] = []
            self.keyword_cache[uid].append(kw)

        logger.info(
            f"Loaded {len(all_keywords)} keywords for {len(self.keyword_cache)} users."
        )

        c.execute("SELECT user_id, seconds FROM user_settings")
        for uid, sec in c.fetchall():
            self.cooldown_settings[uid] = sec

        conn.close()

        logger.info("Data loaded successfully.")

    async def setup_hook(self):
        logger.info("Setting up database...")

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_keywords (user_id INTEGER, keyword TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, seconds INTEGER, permission_verified INTEGER DEFAULT 0)"
        )

        conn.commit()
        conn.close()

        logger.info("Database setup complete.")

        self.load_data()
        await self.tree.sync()

    def is_user_still_cooldown(self, uid, kw):
        user_cooldown = self.cooldown_settings.get(uid, DEFAULT_COOLDOWN)
        last_time = self.last_notified.get((uid, kw), 0)

        return time.time() - last_time < user_cooldown

    async def send_notification(self, uid, message, kw):
        target_user = await self.fetch_user(uid)

        embed = discord.Embed(title=f"🔔 關鍵字 `{kw}` 命中", color=0x3498DB)
        embed.description = f"**內容：** {message.content[:200]}"
        embed.add_field(name="來源", value=f"{message.channel.mention}")
        embed.add_field(name="連結", value=f"[點我跳轉]({message.jump_url})")

        try:
            await target_user.send(embed=embed)
        except discord.Forbidden:
            logger.warning(
                "Failed to send notification to %s(%d): Forbidden", target_user, uid
            )
        except Exception as e:
            logger.exception(
                "Error sending notification to %s(%d): %s", target_user, uid, e
            )

        logger.info(
            "Sending notification to %s(%d) for keyword '%s' in message: %s",
            target_user,
            uid,
            kw,
            message.content,
        )

    def update_last_notified(self, uid, kw):
        self.last_notified[(uid, kw)] = time.time()

    def is_keyword_in_string(self, string, kw):
        """
        ignore custom emojis, standard emojis, and URLs when checking for keyword presence
        """
        string = string.lower()
        string = re.sub(r"<:\w+:\d+>", "", string)
        string = re.sub(r"<@\d+>", "", string)
        string = re.sub(r":\w+:", "", string)
        string = re.sub(r"https?://\S+", "", string)

        return kw in string

    def is_trigger_keyword(self, message, kw):
        result = False

        # check content first
        if self.is_keyword_in_string(message.content, kw):
            result = True

        # then check embeds
        for embed in message.embeds:
            if embed.title and self.is_keyword_in_string(embed.title, kw):
                result = True
                break
            if embed.description and self.is_keyword_in_string(embed.description, kw):
                result = True
                break
            for field in embed.fields:
                if self.is_keyword_in_string(
                    field.name, kw
                ) or self.is_keyword_in_string(field.value, kw):
                    result = True
                    break
            if result:
                break

        return result

    async def cache_guild_members(self, guild):
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

    async def warm_member_cache(self):
        for guild in self.guilds:
            await self.cache_guild_members(guild)

    async def is_user_in_same_guild(self, uid, message):
        if message.guild is None:
            return False

        guild_id = message.guild.id
        members = self.guild_member_ids.get(guild_id)
        if members is not None:
            return uid in members

        if message.guild.get_member(uid) is not None:
            return True

        await self.cache_guild_members(message.guild)
        members = self.guild_member_ids.get(guild_id)
        return uid in members if members is not None else False

    def has_permission_verified(self, uid):
        # Check if user has already verified permissions
        conn = sqlite3.connect(self.db_path)
        result = conn.execute(
            "SELECT permission_verified FROM user_settings WHERE user_id = ?", (uid,)
        ).fetchone()
        conn.close()
        return result[0] if result else 0

    async def can_send_permission_test_message(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="✅ 權限測試",
                description="恭喜！Bot 成功發送訊息到你的 DM。你已經可以接收關鍵字通知了。",
                color=0x2ECC71,
            )
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            try:
                await interaction.response.send_message(
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
                await interaction.response.send_message(
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


bot = MyBot()


@bot.tree.command(name="notify_cooldown", description="設定相同關鍵字通知的冷卻時間")
@app_commands.describe(seconds="冷卻時間（秒）")
async def notify_cooldown(interaction: discord.Interaction, seconds: int):
    if seconds < 0:
        try:
            await interaction.response.send_message("秒數不能為負數！", ephemeral=True)
        except Exception as e:
            logger.exception(
                "Error sending cooldown error message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    uid = interaction.user.id
    conn = sqlite3.connect(bot.db_path)
    conn.execute(
        """
        INSERT INTO user_settings (user_id, seconds)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET seconds = excluded.seconds
        """,
        (uid, seconds),
    )
    conn.commit()
    conn.close()

    bot.cooldown_settings[uid] = seconds

    try:
        await interaction.response.send_message(
            f"✅ 冷卻時間已設定為 `{seconds}` 秒。", ephemeral=True
        )
    except Exception as e:
        logger.exception(
            "Error sending cooldown confirmation to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) set cooldown to %d seconds", interaction.user, uid, seconds
    )


@bot.tree.command(name="notify_add", description="訂閱關鍵字通知")
@app_commands.describe(keyword="要訂閱的關鍵字（用 , 分隔）")
async def notify_add(interaction: discord.Interaction, keyword: str):
    keywords = keyword.lower().strip().split(",")
    uid = interaction.user.id
    permission_verified = bot.has_permission_verified(uid)

    # Send test message only if permissions haven't been verified yet
    if not permission_verified:
        if not await bot.can_send_permission_test_message(interaction):
            logger.warning(
                "User %s(%d) failed permission verification", interaction.user, uid
            )
            return

        # Mark permission as verified only after successful test message
        conn = sqlite3.connect(bot.db_path)
        conn.execute(
            """
            INSERT INTO user_settings (user_id, permission_verified)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET permission_verified = excluded.permission_verified
            """,
            (uid, 1),
        )
        conn.commit()
        conn.close()

    conn = sqlite3.connect(bot.db_path)

    for kw in keywords:
        kw = kw.strip()
        if len(kw) == 0:
            continue

        res = conn.execute(
            "SELECT 1 FROM user_keywords WHERE user_id = ? AND keyword = ?", (uid, kw)
        ).fetchone()
        if res is not None:
            continue

        conn.execute("INSERT INTO user_keywords VALUES (?, ?)", (uid, kw))

    conn.commit()
    conn.close()

    if uid not in bot.keyword_cache:
        bot.keyword_cache[uid] = []
    for kw in keywords:
        if kw not in bot.keyword_cache[uid]:
            bot.keyword_cache[uid].append(kw)
    
    try:
        await interaction.response.send_message(f"✅ 已訂閱：`{keyword}`", ephemeral=True)
    except Exception as e:
        logger.exception(
            "Error sending subscription confirmation to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) is subscribing to keyword: %s", interaction.user, uid, keyword
    )


@bot.tree.command(name="notify_list", description="查看所有訂閱關鍵字")
async def notify_list(interaction: discord.Interaction):
    conn = sqlite3.connect(bot.db_path)
    res = conn.execute(
        "SELECT keyword FROM user_keywords WHERE user_id = ?", (interaction.user.id,)
    )
    rows = res.fetchall()
    keywords = [r[0] for r in rows]
    msg = (
        "你訂閱的關鍵字：\n" + "\n".join(f"- {k}" for k in keywords)
        if keywords
        else "你還沒有訂閱任何關鍵字。"
    )

    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        logger.exception(
            "Error sending keyword list to user %s(%d): %s",
            interaction.user,
            interaction.user.id,
            e,
        )

    logger.info(
        "User %s(%d) requested their keyword list",
        interaction.user,
        interaction.user.id,
    )


@bot.tree.command(name="notify_remove", description="取消訂閱關鍵字通知")
@app_commands.describe(keyword="要取消訂閱的關鍵字（用 , 分隔）")
async def notify_remove(interaction: discord.Interaction, keyword: str):
    keywords = keyword.lower().strip().split(",")
    uid = interaction.user.id

    conn = sqlite3.connect(bot.db_path)
    for kw in keywords:
        kw = kw.strip()
        if len(kw) == 0:
            continue

        res = conn.execute(
            "SELECT 1 FROM user_keywords WHERE user_id = ? AND keyword = ?", (uid, kw)
        ).fetchone()

        if res is None:
            continue

        conn.execute(
            "DELETE FROM user_keywords WHERE user_id = ? AND keyword = ?", (uid, kw)
        )
    conn.commit()
    conn.close()

    if uid in bot.keyword_cache and kw in bot.keyword_cache[uid]:
        bot.keyword_cache[uid].remove(kw)

    try:
        await interaction.response.send_message(
            f"✅ 已取消訂閱：`{keyword}`", ephemeral=True
        )
    except Exception as e:
        logger.exception(
            "Error sending unsubscription confirmation to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) is unsubscribing from keyword: %s", interaction.user, uid, keyword
    )


@bot.event
async def on_ready():
    logger.info("We have logged in as %s", bot.user)
    await bot.warm_member_cache()


@bot.event
async def on_guild_join(guild):
    await bot.cache_guild_members(guild)


@bot.event
async def on_guild_remove(guild):
    bot.guild_member_ids.pop(guild.id, None)


@bot.event
async def on_member_join(member):
    if member.guild.id not in bot.guild_member_ids:
        bot.guild_member_ids[member.guild.id] = set()
    bot.guild_member_ids[member.guild.id].add(member.id)


@bot.event
async def on_member_remove(member):
    members = bot.guild_member_ids.get(member.guild.id)
    if members is not None:
        members.discard(member.id)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    for uid, keywords in bot.keyword_cache.items():
        if message.author.id == uid or message.author.bot:
            continue

        if not await bot.is_user_in_same_guild(uid, message):
            continue

        for kw in keywords:
            if bot.is_trigger_keyword(message, kw):
                if bot.is_user_still_cooldown(uid, kw):
                    continue

                try:
                    await bot.send_notification(uid, message, kw)
                    bot.update_last_notified(uid, kw)
                    break
                except Exception as e:
                    logger.exception(
                        "Exception occurred while notifying user %s: %s", uid, e
                    )


bot.run(TOKEN)
