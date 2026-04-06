import asyncio
import datetime
import re
import sqlite3

import aiohttp
import discord

from config import (
    DB_PATH,
    NOTIFICATION_MAX_DESCRIPTION_LENGTH,
    YT_API_BASE_URL,
    YT_CHANNEL_IDS,
    YT_MEMORY_LIMIT,
    YT_NOTIFY_CHANNEL_ID,
    YT_POLL_INTERVAL,
    logger,
)


class YouTubeLinkView(discord.ui.View):
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


class YouTubeCommunityMixin:
    db_path = DB_PATH

    @staticmethod
    def _parse_relative_date_to_utc(date_text: str) -> datetime.datetime | None:
        if not date_text:
            return None

        text = date_text.strip().lower()
        match = re.match(
            r"^(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$", text
        )
        if not match:
            return None

        value = int(match.group(1))
        unit = match.group(2)

        unit_to_seconds = {
            "second": 1,
            "minute": 60,
            "hour": 3600,
            "day": 86400,
            "week": 604800,
            # Approximation for relative text
            "month": 2592000,
            "year": 31536000,
        }

        seconds = unit_to_seconds.get(unit)
        if seconds is None:
            return None

        return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=value * seconds
        )

    def load_youtube_community_data(self) -> None:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT source_key, post_id FROM yt_community_notified"
        ).fetchall()
        conn.close()

        for source_key, post_id in rows:
            self.yt_community_notified.setdefault(str(source_key), {})[
                str(post_id)
            ] = None

        logger.info(
            "Loaded YT community notified IDs: %d",
            sum(len(v) for v in self.yt_community_notified.values()),
        )

    def remember_youtube_community_notified_id(
        self,
        source_key: str,
        post_id: str,
    ) -> bool:
        source_cache = self.yt_community_notified.setdefault(source_key, {})
        if post_id in source_cache:
            return False

        source_cache[post_id] = None
        while len(source_cache) > YT_MEMORY_LIMIT:
            old_id = next(iter(source_cache))
            source_cache.pop(old_id)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR IGNORE INTO yt_community_notified (source_key, post_id) VALUES (?, ?)",
                (source_key, post_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("Failed to save YT community post ID")

        return True

    async def youtube_community_monitor(self) -> None:
        if not YT_CHANNEL_IDS or not YT_NOTIFY_CHANNEL_ID:
            return

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self.youtube_community_check(session)
                except Exception:
                    logger.exception("YouTube community monitor error")

                await asyncio.sleep(YT_POLL_INTERVAL)

    async def youtube_community_check(self, session: aiohttp.ClientSession) -> None:
        base_url = YT_API_BASE_URL.rstrip("/")

        for source in YT_CHANNEL_IDS:
            source = source.strip()
            if not source:
                continue

            is_handle = source.startswith("@")
            if is_handle:
                source_key = f"handle:{source.lower()}"
            else:
                source_key = f"cid:{source}"

            had_cache_before = bool(self.yt_community_notified.get(source_key))

            params = {"part": "community"}
            if is_handle:
                params["handle"] = source
            else:
                params["id"] = source

            try:
                async with session.get(
                    f"{base_url}/channels", params=params, timeout=20
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "YT community API returned %d for source %s",
                            resp.status,
                            source,
                        )
                        continue
                    payload = await resp.json()
            except Exception:
                logger.exception(
                    "Failed to query YT community API for source %s",
                    source,
                )
                continue

            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                continue

            community = (
                items[0].get("community") if isinstance(items[0], dict) else None
            )
            if not isinstance(community, list) or not community:
                continue

            if not had_cache_before:
                logger.info(
                    "YT community warm-up for source %s: caching existing posts without notifications",
                    source,
                )

            # Oldest first so multiple new posts are notified in order.
            for post in reversed(community):
                post_id = str(post.get("id") or "").strip()
                if not post_id:
                    continue

                if not self.remember_youtube_community_notified_id(source_key, post_id):
                    continue

                # Warm up cache on first fetch to avoid flooding historical posts.
                # if not had_cache_before:
                #     continue

                logger.info("Detected new YT community post: %s (%s)", post_id, source)
                await self.send_youtube_community_notification(
                    post, YT_NOTIFY_CHANNEL_ID
                )
                await asyncio.sleep(1)

    @staticmethod
    def _normalize_yt_content_text(content_text: list[dict]) -> str:
        parts: list[str] = []
        for token in content_text:
            if not isinstance(token, dict):
                continue

            text = str(token.get("text") or "")
            url = token.get("url")
            if not text:
                continue

            if url and isinstance(url, str) and url.startswith("http"):
                parts.append(f"[{text}]({url})")
            else:
                parts.append(text)

        return "".join(parts).strip()

    async def send_youtube_community_notification(
        self,
        post: dict,
        notify_channel_id: int,
    ) -> None:
        channel = self.get_channel(notify_channel_id)
        if not channel:
            logger.warning("YT notify channel %s not found", notify_channel_id)
            return

        if hasattr(
            self, "has_send_embed_permissions"
        ) and not self.has_send_embed_permissions(channel):
            return

        post_id = post.get("id")
        channel_name = post.get("channelName") or "Unknown Channel"
        post_url = f"https://www.youtube.com/post/{post_id}" if post_id else None

        content_text = post.get("contentText")
        text = ""
        if isinstance(content_text, list):
            text = self._normalize_yt_content_text(content_text)

        if len(text) > NOTIFICATION_MAX_DESCRIPTION_LENGTH:
            text = text[:NOTIFICATION_MAX_DESCRIPTION_LENGTH] + "..."

        embed = discord.Embed(
            title=f"📝 YouTube 社群貼文：{channel_name}",
            description=text or "(無文字內容)",
            color=0xFF0000,
            url=post_url,
        )

        thumbnails = post.get("channelThumbnails")
        author_icon = None
        if isinstance(thumbnails, list) and thumbnails:
            first = thumbnails[0]
            if isinstance(first, dict):
                author_icon = first.get("url")

        channel_handle = post.get("channelHandle")
        author_name = (
            f"{channel_name} ({channel_handle})"
            if channel_handle
            else str(channel_name)
        )

        if author_icon:
            embed.set_author(name=author_name, icon_url=author_icon)
        else:
            embed.set_author(name=author_name)

        images = post.get("images")
        if isinstance(images, list) and images:
            first_image = images[0]
            if isinstance(first_image, dict):
                image_thumbnails = first_image.get("thumbnails")
                if isinstance(image_thumbnails, list) and image_thumbnails:
                    first_thumb = image_thumbnails[-1]
                    if isinstance(first_thumb, dict):
                        image_url = first_thumb.get("url")
                        if image_url:
                            embed.set_image(url=image_url)

        likes = post.get("likes")
        comments_count = post.get("commentsCount")
        date_text = post.get("date")

        if likes is not None or comments_count is not None:
            stat_parts = []
            if likes is not None:
                stat_parts.append(f"👍 {likes}")
            if comments_count is not None:
                stat_parts.append(f"💬 {comments_count}")
            embed.add_field(name="互動", value=" | ".join(stat_parts), inline=False)

        if date_text:
            parsed_date = self._parse_relative_date_to_utc(str(date_text))
            if parsed_date is not None:
                embed.timestamp = parsed_date

        view = YouTubeLinkView(post_url) if post_url else None

        try:
            await channel.send(embed=embed, view=view)
        except Exception:
            logger.exception("Failed to send YT community notification")
