import asyncio
import datetime
import logging
import os
import re

from dotenv import load_dotenv
import aiohttp
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

# Add file handler for ERROR logs
error_log_path = os.getenv("ERROR_LOG_PATH", "error.log")
file_handler = logging.FileHandler(error_log_path, encoding="utf-8")
file_handler.setLevel(logging.ERROR)
file_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
file_handler.setFormatter(file_formatter)
logging.getLogger().addHandler(file_handler)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Please set it in your environment or in a .env file."
    )

DB_PATH = os.getenv("DB_PATH", "keywords.db")
DEFAULT_COOLDOWN = int(os.getenv("DEFAULT_COOLDOWN", "30"))

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

        # Keep a bounded history of notified IDs to avoid duplicate alerts.
        # { source_key: { stream_or_video_id: None } }
        self.holodex_notified_live = {}
        self.holodex_notified_upcoming = {}
        self.holodex_notified_upload = {}
        self.guild_member_ids = {}  # { guild_id: set(user_id) }

        # In-memory dedupe for keyword notification (message_id:keyword)
        self.notified_message_keywords = set()  # Set[str], key = f"{message_id}:{keyword}"

    def remember_holodex_notified_id(
        self, cache: dict, source_key: str, item_id: str, notify_type: str = "live"
    ) -> bool:
        source_cache = cache.setdefault(source_key, {})
        if item_id in source_cache:
            return False

        source_cache[item_id] = None

        while len(source_cache) > HOLODEX_MEMORY_LIMIT:
            old_id = next(iter(source_cache))
            source_cache.pop(old_id)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR IGNORE INTO holodex_notified (source_key, item_id, notify_type) VALUES (?, ?, ?)",
                (source_key, item_id, notify_type),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.exception("Failed to save Holodex notified ID to database: %s", e)

        return True

    def is_holodex_plain_video(self, video: dict) -> bool:
        # For Holodex live_info payloads, plain videos usually have live_viewers = null.
        return video.get("live_viewers") is None

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

        res = c.execute("SELECT user_id, seconds FROM user_settings")
        for uid, sec in res.fetchall():
            self.cooldown_settings[uid] = sec
        logger.info(
            f"Loaded cooldown settings for {len(self.cooldown_settings)} users."
        )

        res = c.execute("SELECT source_key, item_id, notify_type FROM holodex_notified")
        for source_key, item_id, notify_type in res.fetchall():
            if notify_type == "live":
                self.holodex_notified_live.setdefault(source_key, {})[item_id] = None
            elif notify_type == "upcoming":
                self.holodex_notified_upcoming.setdefault(source_key, {})[
                    item_id
                ] = None
            elif notify_type == "upload":
                self.holodex_notified_upload.setdefault(source_key, {})[item_id] = None
        logger.info(
            f"Loaded Holodex notified IDs: {sum(len(v) for v in self.holodex_notified_live.values())} live, "
            f"{sum(len(v) for v in self.holodex_notified_upcoming.values())} upcoming, "
            f"{sum(len(v) for v in self.holodex_notified_upload.values())} upload."
        )

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
        conn.execute(
            "CREATE TABLE IF NOT EXISTS holodex_notified (source_key TEXT, item_id TEXT, notify_type TEXT, PRIMARY KEY (source_key, item_id, notify_type))"
        )

        conn.commit()
        conn.close()

        logger.info("Database setup complete.")

        self.load_data()

        await self.tree.sync()

    def is_user_still_cooldown(self, uid: int, kw: str) -> bool:
        user_cooldown = self.cooldown_settings.get(uid, DEFAULT_COOLDOWN)
        last_time = self.last_notified.get((uid, kw), 0)

        return time.time() - last_time < user_cooldown

    async def holodex_live_monitor(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self.holodex_check_live(session)
                except Exception:
                    logger.exception("Holodex live monitor error")
                await asyncio.sleep(HOLODEX_POLL_INTERVAL)

    async def holodex_check_live(self, session: aiohttp.ClientSession) -> None:
        if not HOLODEX_ORG and not HOLODEX_CHANNEL_IDS:
            return

        headers = {}
        if HOLODEX_API_KEY:
            headers["X-APIKEY"] = HOLODEX_API_KEY

        sources = [HOLODEX_ORG] if HOLODEX_ORG else HOLODEX_CHANNEL_IDS
        is_org = bool(HOLODEX_ORG)

        for source in sources:
            if is_org:
                live_url = f"https://holodex.net/api/v2/live?org={source}&include=live_info,description"
            else:
                live_url = f"https://holodex.net/api/v2/live?channel_id={source}&include=live_info,description"

            try:
                async with session.get(live_url, headers=headers, timeout=20) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Holodex API returned %d for source %s", resp.status, source
                        )
                        continue
                    data = await resp.json()
            except Exception:
                logger.exception("Failed to query Holodex for source %s", source)
                continue

            source_key = f"org:{source}" if is_org else f"cid:{source}"
            if isinstance(data, list) and data:
                for stream in data:
                    stream_id = stream.get("id") or stream.get("video_id")
                    status = (
                        stream.get("status") or stream.get("live_status") or ""
                    ).lower()
                    stream_channel_id = (
                        stream.get("channel", {}).get("id")
                        or stream.get("channel_id")
                        or stream.get("owner", {}).get("id")
                        or None
                    )
                    dedupe_key = (
                        f"cid:{stream_channel_id}" if stream_channel_id else source_key
                    )

                    if not stream_id:
                        logger.warning("Holodex stream missing id: %s", stream)
                        continue

                    if status == "live" or stream.get("is_live"):
                        if self.remember_holodex_notified_id(
                            self.holodex_notified_live, dedupe_key, stream_id, "live"
                        ):
                            logger.info(
                                "Detected new live stream for source %s: %s",
                                source,
                                stream_id,
                            )
                            if HOLODEX_NOTIFY_LIVE_CHANNEL_ID:
                                await self.send_holodex_status_notification(
                                    stream, HOLODEX_NOTIFY_LIVE_CHANNEL_ID, "live"
                                )
                        continue

                    if status == "upcoming" or stream.get("is_upcoming"):
                        if self.remember_holodex_notified_id(
                            self.holodex_notified_upcoming,
                            dedupe_key,
                            stream_id,
                            "upcoming",
                        ):
                            logger.info(
                                "Detected new upcoming stream for source %s: %s",
                                source,
                                stream_id,
                            )
                            if HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID:
                                await self.send_holodex_status_notification(
                                    stream,
                                    HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID,
                                    "upcoming",
                                )
                        continue

            # 2) Check latest uploads (limit 5 to handle multiple new videos)
            if is_org:
                upload_url = f"https://holodex.net/api/v2/videos?org={source}&sort=published_at&limit=5&type=stream&include=live_info,description"
            else:
                upload_url = f"https://holodex.net/api/v2/videos?channel_id={source}&sort=published_at&limit=5&type=stream&include=live_info,description"

            try:
                async with session.get(upload_url, headers=headers, timeout=20) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Holodex upload API returned %d for source %s",
                            resp.status,
                            source,
                        )
                        continue
                    upload_data = await resp.json()
            except Exception:
                logger.exception(
                    "Failed to query Holodex uploads for source %s", source
                )
                continue

            if isinstance(upload_data, list) and upload_data:
                # reverse so we send older content first
                for video in reversed(upload_data):
                    video_id = video.get("id") or video.get("video_id")

                    if not self.is_holodex_plain_video(video):
                        continue

                    video_channel_id = (
                        video.get("channel", {}).get("id")
                        or video.get("channel_id")
                        or video.get("owner", {}).get("id")
                        or None
                    )
                    video_key = (
                        f"cid:{video_channel_id}" if video_channel_id else source_key
                    )

                    if not video_id:
                        continue
                    if not self.remember_holodex_notified_id(
                        self.holodex_notified_upload, video_key, video_id, "upload"
                    ):
                        continue

                    logger.info(
                        "Detected new upload for source %s: %s", source, video_id
                    )
                    if HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID:
                        await self.send_holodex_status_notification(
                            video, HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID, "upload"
                        )

    async def send_holodex_status_notification(
        self, stream: dict, notify_channel_id: int, stream_type: str
    ) -> None:
        if not notify_channel_id:
            return

        channel = self.get_channel(notify_channel_id)
        if not channel:
            logger.warning("Holodex notify channel %s not found", notify_channel_id)
            return

        if not self.has_send_embed_permissions(channel):
            return

        stream_title = stream.get("title") or stream.get("video_title") or "No title"
        channel_name = stream.get("channel", {}).get("name") or stream.get(
            "channel_name"
        )
        stream_id = stream.get("id") or stream.get("video_id")
        stream_url = stream.get("url") or stream.get("video_url")
        if not stream_url and stream_id:
            stream_url = f"https://www.youtube.com/watch?v={stream_id}"

        if stream_type == "upcoming":
            stream_time_raw = (
                stream.get("start_scheduled")
                or stream.get("available_at")
                or stream.get("published_at")
            )
        elif stream_type == "live":
            stream_time_raw = stream.get("start_actual") or stream.get("published_at")
        else:
            stream_time_raw = stream.get("published_at") or stream.get("available_at")

        if stream_type == "live":
            title = f"🔴 直播開始：{stream_title or ''}"
            color = 0xE74C3C
        elif stream_type == "upcoming":
            title = f"⏰ 即將直播：{stream_title or ''}"
            color = 0xF1C40F
        else:
            title = f"🎬 新影片：{stream_title or ''}"
            color = 0x3498DB

        desc_text = stream.get("description") or stream.get("display_message") or ""
        embed_description = ""
        if desc_text:
            short_desc = desc_text[:700] + ("..." if len(desc_text) > 700 else "")
            embed_description = "> " + "\n> ".join(short_desc.splitlines())

        embed = discord.Embed(
            title=title,
            description=embed_description,
            url=stream_url,
            color=color,
        )

        parsed_stream_time = None
        if stream_time_raw:
            try:
                parsed_stream_time = datetime.datetime.fromisoformat(
                    stream_time_raw.replace("Z", "+00:00")
                )
                embed.timestamp = parsed_stream_time
            except Exception:
                pass

        channel_icon = stream.get("channel", {}).get("photo")
        if channel_name and channel_icon:
            embed.set_author(name=channel_name, icon_url=channel_icon)
        elif channel_name:
            embed.set_author(name=channel_name)

        image_url = (
            f"https://i.ytimg.com/vi/{stream_id}/sddefault.jpg"
            if stream_id
            else (stream.get("thumbnail"))
        )
        if image_url:
            embed.set_image(url=image_url)

        if channel_icon:
            embed.set_thumbnail(url=channel_icon)

        if stream_url:
            embed.add_field(name="傳送門", value=stream_url, inline=False)

        if stream_type == "upcoming" and parsed_stream_time is not None:
            start_ts = int(parsed_stream_time.timestamp())
            embed.add_field(
                name="開播時間",
                value=f"<t:{start_ts}:F>\n(<t:{start_ts}:R>)",
                inline=False,
            )

        try:
            await channel.send(embed=embed)
        except Exception:
            logger.exception(
                "Failed to send Holodex %s notification message", stream_type
            )

    async def send_notification(
        self, uid: int, message: discord.Message, kw: str
    ) -> None:
        target_user = await self.fetch_user(uid)

        # get a image url from attachments or embeds if available
        image_url = None
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith(
                    "image/"
                ):
                    image_url = attachment.url
                    break
        elif message.embeds:
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    image_url = embed.image.url
                    break
                if embed.thumbnail and embed.thumbnail.url:
                    image_url = embed.thumbnail.url
                    break

        embed = discord.Embed(
            title=f"🔔 關鍵字 `{kw}` 命中",
            color=0x3498DB,
            timestamp=message.created_at,
            url=message.jump_url,
        )

        server_name = message.guild.name if message.guild else "私人訊息"
        channel_name = message.channel.name if message.channel else "未知頻道"
        server_icon = (
            message.guild.icon.url if message.guild and message.guild.icon else None
        )

        embed.description = (
            f"{message.content[:200]}{'...' if len(message.content) > 200 else ''}"
        )

        # try to emulate the message preview by extracting info from embeds
        author_icon_url = None
        if message.embeds:
            embed_parts: list[str] = []
            for orig in message.embeds:
                section: list[str] = []
                if orig.author and orig.author.name:
                    author_image = (
                        orig.author.icon_url if orig.author.icon_url else None
                    )
                    author_name = orig.author.name
                    if author_image and not author_icon_url:
                        author_icon_url = author_image
                    section.append(f"**{author_name}**\n")
                if orig.title:
                    section.append(f"**{orig.title}**\n")
                if orig.description:
                    section.append(orig.description)
                for field in orig.fields:
                    section.append(f"**{field.name}** {field.value}")
                if section:
                    quoted = "> " + "\n> ".join("\n".join(section).splitlines())
                    embed_parts.append(quoted)

            if embed_parts:
                nested = "\n\n".join(embed_parts)
                nested = f"{nested[:200]}{'...' if len(nested) > 200 else ''}"
                embed.add_field(name="\u200b", value=nested, inline=True)

        if not image_url and author_icon_url:
            embed.set_thumbnail(url=author_icon_url)

        embed.add_field(
            name="\u200b", value=f"[傳送門]({message.jump_url})", inline=False
        )

        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(text=f"{server_name}﹥＃{channel_name}", icon_url=server_icon)

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

    def update_last_notified(self, uid: int, kw: str) -> None:
        self.last_notified[(uid, kw)] = time.time()

    def is_keyword_in_string(self, string: str, kw: str) -> bool:
        """
        Message Formatting according to Discord's Documentation:
        https://docs.discord.com/developers/reference#message-formatting

        ignore emojis, user id, channel id, and URLs...
        when checking for keyword presence
        """
        string = string.lower()
        string = re.sub(r"<@\d+>", "", string)
        string = re.sub(r"<@!\d+>", "", string)
        string = re.sub(r"<#\d+>", "", string)
        string = re.sub(r"<@&\d+>", "", string)
        string = re.sub(r"</\w+:\d+>", "", string)
        string = re.sub(r"<:\w+:\d+>", "", string)
        string = re.sub(r"<a:\w+:\d+>", "", string)
        string = re.sub(r"<t:\d+>", "", string)
        string = re.sub(r"<t:\d+:\w>", "", string)
        string = re.sub(r"<id:\w>", "", string)
        string = re.sub(r":\w+:", "", string)
        string = re.sub(r"https?://\S+", "", string)

        return kw in string

    def is_trigger_keyword(self, message: discord.Message, kw: str) -> bool:
        result = False

        # check content first
        if self.is_keyword_in_string(message.content, kw):
            result = True

        # then check embeds
        for embed in message.embeds:
            if embed.title and self.is_keyword_in_string(embed.title, kw):
                result = True
                break
            if (
                embed.author
                and embed.author.name
                and self.is_keyword_in_string(embed.author.name, kw)
            ):
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

    async def is_user_in_same_guild(self, uid: int, message: discord.Message) -> bool:
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

    def has_permission_verified(self, uid: int) -> bool:
        # Check if user has already verified permissions
        conn = sqlite3.connect(self.db_path)
        result = conn.execute(
            "SELECT permission_verified FROM user_settings WHERE user_id = ?", (uid,)
        ).fetchone()
        conn.close()
        return result[0] if result else 0

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

    async def check_and_notify(self, message: discord.Message) -> None:
        for uid, keywords in bot.keyword_cache.items():
            if message.author.id == uid:
                continue

            if not await bot.is_user_in_same_guild(uid, message):
                continue

            for kw in keywords:
                if not bot.is_trigger_keyword(message, kw):
                    continue

                if bot.is_user_still_cooldown(uid, kw):
                    continue

                notification_key = f"{message.id}:{uid}:{kw}"
                if notification_key in self.notified_message_keywords:
                    continue

                try:
                    await bot.send_notification(uid, message, kw)
                    bot.update_last_notified(uid, kw)
                    self.notified_message_keywords.add(notification_key)
                    break
                except Exception as e:
                    logger.exception(
                        "Exception occurred while notifying user %s: %s", uid, e
                    )
                    # don't mark as notified if failing

        # prevent memory leak
        if len(self.notified_message_keywords) > 5000:
            for _ in range(1000):
                self.notified_message_keywords.pop()


    def has_send_embed_permissions(self, channel: discord.TextChannel) -> bool:
        permissions = channel.permissions_for(channel.guild.me)
        if (
            not permissions.view_channel
            or not permissions.send_messages
            or not permissions.embed_links
            or not permissions.attach_files
        ):
            logger.warning(
                "Missing permissions for channel %s(%d): view_channel=%s, send_messages=%s, embed_links=%s, attach_files=%s",
                channel.name,
                channel.id,
                permissions.view_channel,
                permissions.send_messages,
                permissions.embed_links,
                permissions.attach_files,
            )
            return False
        return True


bot = MyBot()


@bot.tree.command(name="notify_cooldown", description="設定相同關鍵字通知的冷卻時間")
@app_commands.describe(seconds="冷卻時間（秒）")
async def notify_cooldown(interaction: discord.Interaction, seconds: int):
    await interaction.response.defer(ephemeral=True)

    if seconds < 0:
        try:
            await interaction.followup.send("秒數不能為負數！", ephemeral=True)
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
        await interaction.followup.send(
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
    await interaction.response.defer(ephemeral=True)

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
        original_seconds = bot.cooldown_settings.get(uid, DEFAULT_COOLDOWN)
        conn.execute(
            """
            INSERT INTO user_settings (user_id, permission_verified, seconds)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET permission_verified = excluded.permission_verified, seconds = excluded.seconds
            """,
            (uid, 1, original_seconds),
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
        await interaction.followup.send(f"✅ 已訂閱：`{keyword}`", ephemeral=True)
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
    await interaction.response.defer(ephemeral=True)

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
        await interaction.followup.send(msg, ephemeral=True)
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
    await interaction.response.defer(ephemeral=True)

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

        if uid in bot.keyword_cache and kw in bot.keyword_cache[uid]:
            bot.keyword_cache[uid].remove(kw)
    conn.commit()
    conn.close()

    try:
        await interaction.followup.send(f"✅ 已取消訂閱：`{keyword}`", ephemeral=True)
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

    if (HOLODEX_CHANNEL_IDS or HOLODEX_ORG) and (
        HOLODEX_NOTIFY_LIVE_CHANNEL_ID
        or HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID
        or HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID
    ):
        logger.info(
            "Starting Holodex monitor for channels: %s (interval %ds)",
            HOLODEX_CHANNEL_IDS or HOLODEX_ORG,
            HOLODEX_POLL_INTERVAL,
        )
        bot.loop.create_task(bot.holodex_live_monitor())


@bot.event
async def on_guild_join(guild: discord.Guild):
    await bot.cache_guild_members(guild)


@bot.event
async def on_guild_remove(guild: discord.Guild):
    bot.guild_member_ids.pop(guild.id, None)


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id not in bot.guild_member_ids:
        bot.guild_member_ids[member.guild.id] = set()
    bot.guild_member_ids[member.guild.id].add(member.id)


@bot.event
async def on_member_remove(member: discord.Member):
    members = bot.guild_member_ids.get(member.guild.id)
    if members is not None:
        members.discard(member.id)


@bot.event
async def on_message(message: discord.Message):
    if bot.user in message.mentions:
        await message.reply("<:hoeh:1484208659658576143>")
        return

    if message.author == bot.user or message.author.bot:
        return

    await bot.check_and_notify(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author == bot.user or after.author.bot:
        return

    if len(before.embeds) == 0 and len(after.embeds) > 0:
        logger.debug(f"偵測到訊息產生預覽 Embed: {after.id}")
        await bot.check_and_notify(after)
    elif before.content != after.content:
        logger.debug(f"偵測到訊息改變: {after.id}")
        await bot.check_and_notify(after)


bot.run(TOKEN)
