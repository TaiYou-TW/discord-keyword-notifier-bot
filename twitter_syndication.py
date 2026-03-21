import asyncio
import datetime
import json
import re
import sqlite3

import aiohttp
import discord

from config import (
    DB_PATH,
    NOTIFICATION_MAX_DESCRIPTION_LENGTH,
    TWITTER_MEMORY_LIMIT,
    TWITTER_NOTIFY_CHANNEL_ID,
    TWITTER_POLL_INTERVAL,
    TWITTER_SCREEN_NAMES,
    TWITTER_SYNDICATION_USER_AGENT,
    logger,
)


class TwitterSyndicationMixin:
    db_path = DB_PATH

    def load_twitter_profile_data(self) -> None:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT screen_name, tweet_id FROM twitter_profile_notified"
        ).fetchall()
        conn.close()

        for screen_name, tweet_id in rows:
            key = str(screen_name).lower()
            self.twitter_profile_notified.setdefault(key, {})[str(tweet_id)] = None

        logger.info(
            "Loaded Twitter profile notified IDs: %d",
            sum(len(v) for v in self.twitter_profile_notified.values()),
        )

    def remember_twitter_notified_id(self, screen_name: str, tweet_id: str) -> bool:
        key = screen_name.lower()
        source_cache = self.twitter_profile_notified.setdefault(key, {})
        if tweet_id in source_cache:
            return False

        source_cache[tweet_id] = None
        while len(source_cache) > TWITTER_MEMORY_LIMIT:
            old_id = next(iter(source_cache))
            source_cache.pop(old_id)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR IGNORE INTO twitter_profile_notified (screen_name, tweet_id) VALUES (?, ?)",
                (key, tweet_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("Failed to save Twitter notified ID")

        return True

    async def twitter_profile_monitor(self) -> None:
        if not TWITTER_SCREEN_NAMES or not TWITTER_NOTIFY_CHANNEL_ID:
            return

        headers = {"User-Agent": TWITTER_SYNDICATION_USER_AGENT}
        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                try:
                    await self.twitter_check_profiles(session)
                except Exception:
                    logger.exception("Twitter profile monitor error")
                await asyncio.sleep(TWITTER_POLL_INTERVAL)

    async def twitter_check_profiles(self, session: aiohttp.ClientSession) -> None:
        for raw_name in TWITTER_SCREEN_NAMES:
            screen_name = raw_name.strip()
            if not screen_name:
                continue

            try:
                tweets = await self.fetch_profile_tweets(session, screen_name)
            except Exception:
                logger.exception(
                    "Failed to fetch Twitter profile timeline: %s", screen_name
                )
                continue

            if not tweets:
                continue

            key = screen_name.lower()
            had_cache_before = bool(self.twitter_profile_notified.get(key))

            for tweet in tweets:
                tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
                if not tweet_id:
                    continue

                if not self.remember_twitter_notified_id(screen_name, tweet_id):
                    continue

                # Warm up cache on first fetch to avoid flooding old tweets.
                if not had_cache_before:
                    continue

                logger.info("Detected new tweet for @%s: %s", screen_name, tweet_id)
                await self.send_twitter_tweet_notification(
                    screen_name=screen_name,
                    tweet=tweet,
                    notify_channel_id=TWITTER_NOTIFY_CHANNEL_ID,
                )

    async def fetch_profile_tweets(
        self, session: aiohttp.ClientSession, screen_name: str
    ) -> list[dict]:
        url = (
            "https://syndication.twitter.com/srv/timeline-profile/"
            f"screen-name/{screen_name}"
        )

        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Twitter syndication API returned {resp.status}")
            html = await resp.text()

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
        if not match:
            logger.warning("No __NEXT_DATA__ payload for @%s", screen_name)
            return []

        payload = json.loads(match.group(1))
        entries = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("timeline", {})
            .get("entries", [])
        )

        tweets: list[dict] = []
        for entry in entries:
            tweet = (entry.get("content") or {}).get("tweet")
            if tweet:
                tweets.append(tweet)

        # Send older tweets first when multiple new tweets appear in one poll.
        tweets.sort(key=lambda t: str(t.get("id_str") or t.get("id") or ""))
        return tweets

    async def send_twitter_tweet_notification(
        self, screen_name: str, tweet: dict, notify_channel_id: int
    ) -> None:
        channel = self.get_channel(notify_channel_id)
        if not channel:
            logger.warning("Twitter notify channel %s not found", notify_channel_id)
            return

        if hasattr(
            self, "has_send_embed_permissions"
        ) and not self.has_send_embed_permissions(channel):
            return

        user = tweet.get("user") or {}
        user_name = user.get("name") or user.get("screen_name") or "Unknown User"
        user_screen_name = user.get("screen_name") or screen_name or "unknown"
        avatar = user.get("profile_image_url_https") or user.get("profile_image_url")

        tweet_id = tweet.get("id_str") or tweet.get("id")
        tweet_url = (
            f"https://x.com/{user_screen_name}/status/{tweet_id}" if tweet_id else None
        )

        full_text = tweet.get("full_text") or tweet.get("text") or ""
        short_text = full_text[:NOTIFICATION_MAX_DESCRIPTION_LENGTH]
        if len(full_text) > NOTIFICATION_MAX_DESCRIPTION_LENGTH:
            short_text += "..."

        embed = discord.Embed(
            title=f"🐦 Twitter 新推文 (@{user_screen_name})",
            description=short_text or "(無文字內容)",
            color=0x1DA1F2,
            url=tweet_url,
        )

        if user_name:
            embed.set_author(name=f"{user_name} (@{user_screen_name})", icon_url=avatar)

        media = (tweet.get("entities") or {}).get("media") or []
        if media:
            image_url = media[0].get("media_url_https") or media[0].get("media_url")
            if image_url:
                embed.set_image(url=image_url)

        created_at = tweet.get("created_at")
        if created_at:
            try:
                embed.timestamp = datetime.datetime.strptime(
                    created_at, "%a %b %d %H:%M:%S %z %Y"
                )
            except Exception:
                pass

        if tweet_url:
            embed.add_field(name="連結", value=tweet_url, inline=False)

        try:
            await channel.send(embed=embed)
        except Exception:
            logger.exception("Failed to send Twitter tweet notification")
