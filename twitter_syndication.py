import asyncio
import datetime
import json
import re
import sqlite3
import time

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
    TWITTER_RATE_LIMIT_RESERVE,
    TWITTER_WAIT_BETWEEN_PROFILES,
    TWITTER_WORKER_COUNT,
    TWITTER_WORKER_START_DELAY,
    logger,
)


class TwitterRateLimitedError(Exception):
    pass


class TwitterSyndicationMixin:
    db_path = DB_PATH

    def init_twitter_rate_limit_state(self) -> None:
        self.twitter_rate_limit_lock = asyncio.Lock()
        self.twitter_rate_limit_limit = None
        self.twitter_rate_limit_remaining = None
        self.twitter_rate_limit_reset_epoch = None

    async def wait_for_rate_limit_slot(self) -> None:
        while True:
            sleep_seconds = 0

            async with self.twitter_rate_limit_lock:
                now = time.time()
                if (
                    self.twitter_rate_limit_reset_epoch is not None
                    and now >= self.twitter_rate_limit_reset_epoch
                ):
                    self.twitter_rate_limit_limit = None
                    self.twitter_rate_limit_remaining = None
                    self.twitter_rate_limit_reset_epoch = None

                if (
                    self.twitter_rate_limit_remaining is not None
                    and self.twitter_rate_limit_reset_epoch is not None
                    and now < self.twitter_rate_limit_reset_epoch
                    and self.twitter_rate_limit_remaining <= TWITTER_RATE_LIMIT_RESERVE
                ):
                    sleep_seconds = max(
                        1, int(self.twitter_rate_limit_reset_epoch - now) + 1
                    )
                else:
                    if (
                        self.twitter_rate_limit_remaining is not None
                        and self.twitter_rate_limit_reset_epoch is not None
                        and now < self.twitter_rate_limit_reset_epoch
                        and self.twitter_rate_limit_remaining > 0
                    ):
                        self.twitter_rate_limit_remaining -= 1
                    return

            logger.warning(
                "Twitter rate limit guard waiting %ds (remaining=%s, reserve=%d)",
                sleep_seconds,
                self.twitter_rate_limit_remaining,
                TWITTER_RATE_LIMIT_RESERVE,
            )
            await asyncio.sleep(sleep_seconds)

    async def update_rate_limit_state(self, resp: aiohttp.ClientResponse) -> None:
        limit = resp.headers.get("x-rate-limit-limit")
        remaining = resp.headers.get("x-rate-limit-remaining")
        reset = resp.headers.get("x-rate-limit-reset")

        parsed_limit = None
        parsed_remaining = None
        parsed_reset = None
        try:
            if limit is not None:
                parsed_limit = int(limit)
            if remaining is not None:
                parsed_remaining = int(remaining)
            if reset is not None:
                parsed_reset = int(reset)
        except ValueError:
            return

        async with self.twitter_rate_limit_lock:
            if parsed_limit is not None:
                self.twitter_rate_limit_limit = parsed_limit
            if parsed_remaining is not None:
                self.twitter_rate_limit_remaining = parsed_remaining
            if parsed_reset is not None:
                self.twitter_rate_limit_reset_epoch = parsed_reset

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
            self.init_twitter_rate_limit_state()

            while True:
                worker_groups = self.build_worker_groups(TWITTER_SCREEN_NAMES)
                logger.info(
                    "Starting Twitter round: %d workers for %d profiles",
                    len(worker_groups),
                    len(TWITTER_SCREEN_NAMES),
                )

                tasks = []
                for idx, group in enumerate(worker_groups):
                    initial_delay = idx * TWITTER_WORKER_START_DELAY
                    task = asyncio.create_task(
                        self.twitter_worker_round(
                            session=session,
                            worker_index=idx,
                            profiles=group,
                            initial_delay=initial_delay,
                        )
                    )
                    tasks.append(task)

                round_rate_limited = False
                try:
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_EXCEPTION
                    )

                    for t in done:
                        exc = t.exception()
                        if isinstance(exc, TwitterRateLimitedError):
                            round_rate_limited = True
                            break
                        if exc is not None:
                            logger.exception("Twitter worker failed", exc_info=exc)

                    if round_rate_limited:
                        logger.warning(
                            "429 detected. Canceling all workers and waiting %ds before next round.",
                            TWITTER_POLL_INTERVAL,
                        )
                        for p in pending:
                            p.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                    else:
                        # First-exception wait may return with no pending when all workers finish.
                        await asyncio.gather(*pending, return_exceptions=True)
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()

                await asyncio.sleep(TWITTER_POLL_INTERVAL)

    def build_worker_groups(self, screen_names: list[str]) -> list[list[str]]:
        workers = max(1, TWITTER_WORKER_COUNT)
        workers = min(workers, max(1, len(screen_names)))
        groups: list[list[str]] = [[] for _ in range(workers)]

        for idx, name in enumerate(screen_names):
            groups[idx % workers].append(name)

        return groups

    async def twitter_worker_round(
        self,
        session: aiohttp.ClientSession,
        worker_index: int,
        profiles: list[str],
        initial_delay: int,
    ) -> None:
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)

        logger.info(
            "Worker %d checking %d profiles",
            worker_index,
            len(profiles),
        )

        for raw_name in profiles:
            screen_name = raw_name.strip()
            if not screen_name:
                continue

            try:
                await self.twitter_check_profile(session, screen_name)
            except TwitterRateLimitedError:
                raise
            except Exception:
                logger.exception(
                    "Worker %d failed profile %s",
                    worker_index,
                    screen_name,
                )

            if TWITTER_WAIT_BETWEEN_PROFILES > 0:
                await asyncio.sleep(TWITTER_WAIT_BETWEEN_PROFILES)

    async def twitter_check_profile(
        self, session: aiohttp.ClientSession, screen_name: str
    ) -> None:
        tweets = await self.fetch_profile_tweets(session, screen_name)

        if not tweets:
            return

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

            await asyncio.sleep(
                1
            )  # Small delay between notifications to avoid rate limits

    async def fetch_profile_tweets(
        self, session: aiohttp.ClientSession, screen_name: str
    ) -> list[dict]:
        url = (
            "https://syndication.twitter.com/srv/timeline-profile/"
            f"screen-name/{screen_name}"
        )

        await self.wait_for_rate_limit_slot()

        async with session.get(url, timeout=20) as resp:
            await self.update_rate_limit_state(resp)

            if resp.status == 429:
                raise TwitterRateLimitedError("Twitter syndication API returned 429")
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
