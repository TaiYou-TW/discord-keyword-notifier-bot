import asyncio
import datetime
import sqlite3

import aiohttp
import discord

from config import (
    DB_PATH,
    HOLODEX_API_KEY,
    HOLODEX_CHANNEL_IDS,
    HOLODEX_NOTIFY_LIVE_CHANNEL_ID,
    HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID,
    HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID,
    HOLODEX_ORG,
    HOLODEX_POLL_INTERVAL,
    HOLODEX_MEMORY_LIMIT,
    NOTIFICATION_MAX_DESCRIPTION_LENGTH,
    logger,
)
from enums import HolodexNotifyType


class HolodexLinkView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="傳送門",
                style=discord.ButtonStyle.link,
                url=url,
                emoji="🔗",
            )
        )


class HolodexMixin:
    db_path = DB_PATH

    def remember_holodex_notified_id(
        self,
        cache: dict,
        source_key: str,
        item_id: str,
        notify_type: HolodexNotifyType = HolodexNotifyType.LIVE,
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
                (
                    source_key,
                    item_id,
                    (
                        notify_type.value
                        if isinstance(notify_type, HolodexNotifyType)
                        else str(notify_type)
                    ),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.exception("Failed to save Holodex notified ID to database: %s", e)

        return True

    def load_holodex_status_messages(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT source_key, stream_id, notify_type, channel_id, message_id FROM holodex_status_messages"
            ).fetchall()

        for source_key, stream_id, notify_type, channel_id, message_id in rows:
            try:
                enum_type = HolodexNotifyType(notify_type)
            except ValueError:
                logger.warning(
                    "Unknown Holodex status notify_type in DB: %s", notify_type
                )
                continue

            self.holodex_status_messages.setdefault(enum_type, {}).setdefault(
                source_key, {}
            )[stream_id] = {
                "source_key": source_key,
                "stream_id": stream_id,
                "notify_type": enum_type,
                "channel_id": channel_id,
                "message_id": message_id,
            }

        logger.info(
            "Loaded Holodex status messages: %d live, %d upcoming",
            sum(
                len(v)
                for v in self.holodex_status_messages[HolodexNotifyType.LIVE].values()
            ),
            sum(
                len(v)
                for v in self.holodex_status_messages[
                    HolodexNotifyType.UPCOMING
                ].values()
            ),
        )

    def get_holodex_status_record(
        self, source_key: str, stream_id: str, notify_type: HolodexNotifyType
    ) -> dict | None:
        return (
            self.holodex_status_messages.get(notify_type, {})
            .get(source_key, {})
            .get(stream_id)
        )

    def store_holodex_status_record(
        self,
        source_key: str,
        stream_id: str,
        notify_type: HolodexNotifyType,
        channel_id: int,
        message_id: int,
    ) -> None:
        record = {
            "source_key": source_key,
            "stream_id": stream_id,
            "notify_type": notify_type,
            "channel_id": channel_id,
            "message_id": message_id,
        }
        self.holodex_status_messages.setdefault(notify_type, {}).setdefault(
            source_key, {}
        )[stream_id] = record

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO holodex_status_messages (source_key, stream_id, notify_type, channel_id, message_id) VALUES (?, ?, ?, ?, ?)",
                    (source_key, stream_id, notify_type.value, channel_id, message_id),
                )
        except Exception as e:
            logger.exception("Failed to save Holodex status record: %s", e)

    def remove_holodex_status_record(
        self, source_key: str, stream_id: str, notify_type: HolodexNotifyType
    ) -> None:
        source_cache = self.holodex_status_messages.get(notify_type, {}).get(source_key)
        if source_cache is not None:
            source_cache.pop(stream_id, None)
            if not source_cache:
                self.holodex_status_messages[notify_type].pop(source_key, None)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM holodex_status_messages WHERE source_key = ? AND stream_id = ? AND notify_type = ?",
                    (source_key, stream_id, notify_type.value),
                )
        except Exception as e:
            logger.exception("Failed to remove Holodex status record: %s", e)

    async def delete_holodex_status_message(self, record: dict) -> None:
        channel_id = record.get("channel_id")
        message_id = record.get("message_id")

        if channel_id is not None and message_id is not None:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except Exception:
                    channel = None

            if channel is not None:
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                except Exception:
                    logger.exception(
                        "Failed to delete Holodex status message %s in channel %s",
                        message_id,
                        channel_id,
                    )

        self.remove_holodex_status_record(
            record["source_key"], record["stream_id"], record["notify_type"]
        )

    async def ensure_holodex_status_message(
        self,
        source_key: str,
        stream: dict,
        notify_type: HolodexNotifyType,
        notify_channel_id: int | None,
    ) -> None:
        stream_id = stream.get("id")
        if not stream_id:
            return

        existing_record = self.get_holodex_status_record(
            source_key, stream_id, notify_type
        )
        if existing_record:
            channel_id = existing_record.get("channel_id")
            message_id = existing_record.get("message_id")
            if channel_id is not None and message_id is not None:
                channel = self.get_channel(channel_id)
                if channel is not None:
                    try:
                        await channel.fetch_message(message_id)
                        return
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        await self.delete_holodex_status_message(existing_record)
                else:
                    await self.delete_holodex_status_message(existing_record)
            else:
                await self.delete_holodex_status_message(existing_record)

        if not notify_channel_id:
            return

        sent_message = await self.send_holodex_status_notification(
            stream, notify_channel_id, notify_type
        )
        if sent_message is not None:
            self.store_holodex_status_record(
                source_key,
                stream_id,
                notify_type,
                sent_message.channel.id,
                sent_message.id,
            )

    async def cleanup_holodex_status_messages(
        self,
        source_key: str,
        notify_type: HolodexNotifyType,
        current_stream_ids: set[str],
    ) -> None:
        source_cache = self.holodex_status_messages.get(notify_type, {}).get(source_key)
        if not source_cache:
            return

        for stream_id, record in list(source_cache.items()):
            if stream_id in current_stream_ids:
                continue
            await self.delete_holodex_status_message(record)

    @staticmethod
    def is_holodex_plain_video(video: dict) -> bool:
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
            enum_type = None
            try:
                enum_type = HolodexNotifyType(notify_type)
            except ValueError:
                logger.warning("Unknown notify_type in DB: %s", notify_type)

            if enum_type == HolodexNotifyType.LIVE:
                self.holodex_notified_live.setdefault(source_key, {})[item_id] = None
            elif enum_type == HolodexNotifyType.UPCOMING:
                self.holodex_notified_upcoming.setdefault(source_key, {})[
                    item_id
                ] = None
            elif enum_type == HolodexNotifyType.UPLOAD:
                self.holodex_notified_upload.setdefault(source_key, {})[item_id] = None

        logger.info(
            f"Loaded Holodex notified IDs: {sum(len(v) for v in self.holodex_notified_live.values())} live, "
            f"{sum(len(v) for v in self.holodex_notified_upcoming.values())} upcoming, "
            f"{sum(len(v) for v in self.holodex_notified_upload.values())} upload."
        )

        conn.close()

        logger.info("Data loaded successfully.")

    async def holodex_live_monitor(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                logger.debug("Checking Holodex...")
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
            live_streams: list[dict] = []
            upcoming_streams: list[dict] = []
            if isinstance(data, list) and data:
                for stream in data:
                    stream_id = stream.get("id")
                    status = (stream.get("status") or "").lower()

                    if not stream_id:
                        logger.warning("Holodex stream missing id: %s", stream)
                        continue

                    if status == "live":
                        live_streams.append(stream)
                    elif status == "upcoming":
                        upcoming_streams.append(stream)

            live_stream_ids = {
                str(stream.get("id")) for stream in live_streams if stream.get("id")
            }
            upcoming_stream_ids = {
                str(stream.get("id")) for stream in upcoming_streams if stream.get("id")
            }

            for stream in live_streams:
                await self.ensure_holodex_status_message(
                    source_key,
                    stream,
                    HolodexNotifyType.LIVE,
                    HOLODEX_NOTIFY_LIVE_CHANNEL_ID,
                )

            for stream in upcoming_streams:
                await self.ensure_holodex_status_message(
                    source_key,
                    stream,
                    HolodexNotifyType.UPCOMING,
                    HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID,
                )

            await self.cleanup_holodex_status_messages(
                source_key, HolodexNotifyType.LIVE, live_stream_ids
            )
            await self.cleanup_holodex_status_messages(
                source_key, HolodexNotifyType.UPCOMING, upcoming_stream_ids
            )

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
                    video_id = video.get("id")

                    if not self.is_holodex_plain_video(video):
                        continue

                    video_channel_id = video.get("channel", {}).get("id") or video.get(
                        "channel_id"
                    )
                    video_key = (
                        f"cid:{video_channel_id}" if video_channel_id else source_key
                    )

                    if not video_id:
                        continue
                    if not self.remember_holodex_notified_id(
                        self.holodex_notified_upload,
                        video_key,
                        video_id,
                        HolodexNotifyType.UPLOAD,
                    ):
                        continue

                    logger.info(
                        "Detected new upload for source %s: %s", source, video_id
                    )
                    if HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID:
                        await self.send_holodex_status_notification(
                            video,
                            HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID,
                            HolodexNotifyType.UPLOAD.value,
                        )

    async def send_holodex_status_notification(
        self,
        stream: dict,
        notify_channel_id: int,
        stream_type: HolodexNotifyType | str,
    ) -> discord.Message | None:
        if not notify_channel_id:
            return

        channel = self.get_channel(notify_channel_id)
        if not channel:
            logger.warning("Holodex notify channel %s not found", notify_channel_id)
            return

        if not self.has_send_embed_permissions(channel):
            return

        stream_title = stream.get("title") or "No title"
        channel_name = stream.get("channel", {}).get("name") or "Unknown Channel"
        stream_id = stream.get("id")
        stream_url = stream.get("url")
        if not stream_url and stream_id:
            stream_url = f"https://www.youtube.com/watch?v={stream_id}"

        stream_type_value = (
            stream_type.value
            if isinstance(stream_type, HolodexNotifyType)
            else stream_type
        )

        if stream_type_value == "upcoming":
            stream_time_raw = (
                stream.get("start_scheduled")
                or stream.get("available_at")
                or stream.get("published_at")
            )
        elif stream_type_value == "live":
            stream_time_raw = stream.get("start_actual") or stream.get("published_at")
        else:
            stream_time_raw = stream.get("published_at") or stream.get("available_at")

        if stream_type_value == "live":
            title = f"🔴 直播開始：{stream_title or ''}"
            color = 0xE74C3C
        elif stream_type_value == "upcoming":
            title = f"⏰ 即將直播：{stream_title or ''}"
            color = 0xF1C40F
        else:
            title = f"🎬 新影片：{stream_title or ''}"
            color = 0x3498DB

        desc_text = stream.get("description") or ""
        embed_description = ""
        if desc_text:
            embed_description = desc_text[:NOTIFICATION_MAX_DESCRIPTION_LENGTH] + (
                "..." if len(desc_text) > NOTIFICATION_MAX_DESCRIPTION_LENGTH else ""
            )

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

        view = HolodexLinkView(stream_url) if stream_url else None

        if stream_type_value == "upcoming" and parsed_stream_time is not None:
            start_ts = int(parsed_stream_time.timestamp())
            embed.add_field(
                name="開播時間",
                value=f"<t:{start_ts}:F>\n(<t:{start_ts}:R>)",
                inline=False,
            )

        try:
            return await channel.send(embed=embed, view=view)
        except Exception:
            logger.exception(
                "Failed to send Holodex %s notification message", stream_type
            )
            return None

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
