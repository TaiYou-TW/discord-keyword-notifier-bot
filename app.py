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
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db_path = DB_PATH

        self.keyword_cache = {}  # { user_id: [kw1, kw2] }
        self.cooldown_settings = {}  # { user_id: seconds }
        self.last_notified = {}  # { (user_id, kw): timestamp }

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

        logger.info(f"Loaded {len(all_keywords)} keywords for {len(self.keyword_cache)} users.")

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
            "CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, seconds INTEGER)"
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
        embed.add_field(
            name="來源", value=f"{message.channel.mention}"
        )
        embed.add_field(name="連結", value=f"[點我跳轉]({message.jump_url})")

        await target_user.send(embed=embed)

        logger.info(
            "Sending notification to %s for keyword '%s' in message: %s", target_user, kw, message.content
        )

    def update_last_notified(self, uid, kw):
        self.last_notified[(uid, kw)] = time.time()

    def is_trigger_keyword(self, message, kw):
        result = False

        # ignore emojis, like: <:emoji_name:emoji_id>
        content = re.sub(r'<:\w+:\d+>', '', message.content)

        # check content first
        if kw in content:
            result = True

        # then check embeds
        for embed in message.embeds:
            if embed.title and kw in embed.title:
                result = True
                break
            if embed.description and kw in embed.description:
                result = True
                break
            for field in embed.fields:
                if kw in field.value:
                    result = True
                    break
            if result:
                break

        return result


bot = MyBot()


@bot.tree.command(
    name="notify_cooldown", description="設定相同關鍵字通知的冷卻時間"
)
@app_commands.describe(seconds="冷卻時間（秒）")
async def notify_cooldown(interaction: discord.Interaction, seconds: int):
    if seconds < 0:
        await interaction.response.send_message("秒數不能為負數！", ephemeral=True)
        return

    uid = interaction.user.id
    conn = sqlite3.connect(bot.db_path)
    conn.execute("INSERT OR REPLACE INTO user_settings VALUES (?, ?)", (uid, seconds))
    conn.commit()
    conn.close()

    bot.cooldown_settings[uid] = seconds
    await interaction.response.send_message(
        f"✅ 冷卻時間已設定為 `{seconds}` 秒。", ephemeral=True
    )
    
    logger.info("User %s set cooldown to %d seconds", interaction.user, seconds)


@bot.tree.command(name="notify_add", description="訂閱關鍵字通知")
@app_commands.describe(keyword="要訂閱的關鍵字（用 , 分隔）")
async def notify_add(interaction: discord.Interaction, keyword: str):
    keywords = keyword.lower().strip().split(",")
    uid = interaction.user.id

    conn = sqlite3.connect(bot.db_path)

    for kw in keywords:
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
    await interaction.response.send_message(f"✅ 已訂閱：`{keyword}`", ephemeral=True)

    logger.info("User %s is subscribing to keyword: %s", interaction.user, keyword)


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
    await interaction.response.send_message(msg, ephemeral=True)

    logger.info("User %s requested their keyword list", interaction.user)


@bot.tree.command(name="notify_remove", description="取消訂閱關鍵字通知")
@app_commands.describe(keyword="要取消訂閱的關鍵字（用 , 分隔）")
async def notify_remove(interaction: discord.Interaction, keyword: str):
    keywords = keyword.lower().strip().split(",")
    uid = interaction.user.id

    conn = sqlite3.connect(bot.db_path)
    for kw in keywords:
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

    await interaction.response.send_message(f"✅ 已取消訂閱：`{keyword}`", ephemeral=True)

    logger.info("User %s is unsubscribing from keyword: %s", interaction.user, keyword)


@bot.event
async def on_ready():
    logger.info("We have logged in as %s", bot.user)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    for uid, keywords in bot.keyword_cache.items():
        if message.author.id == uid:
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
                    logger.exception("Exception occurred while notifying user %s: %s", uid, e)


bot.run(TOKEN)
