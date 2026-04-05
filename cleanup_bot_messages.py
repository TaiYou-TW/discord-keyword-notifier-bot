import argparse

import discord

from config import TOKEN, logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete this bot's own messages from specific Discord channels (one-time script)."
        )
    )
    parser.add_argument(
        "channel_ids",
        nargs="+",
        type=int,
        help="Target Discord channel IDs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max messages to scan per channel. Default: scan full history.",
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        default=None,
        help="Max bot messages to delete per channel. Default: no limit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only count candidate messages without deleting.",
    )
    return parser.parse_args()


class CleanupClient(discord.Client):
    def __init__(
        self,
        channel_ids: list[int],
        history_limit: int | None,
        max_delete: int | None,
        dry_run: bool,
    ):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)

        self.channel_ids = channel_ids
        self.history_limit = history_limit
        self.max_delete = max_delete
        self.dry_run = dry_run

    async def on_ready(self):
        logger.info("Logged in as %s (%s)", self.user, self.user.id)

        total_deleted = 0
        total_candidates = 0

        for channel_id in self.channel_ids:
            deleted, candidates = await self._cleanup_channel(channel_id)
            total_deleted += deleted
            total_candidates += candidates

        if self.dry_run:
            logger.info(
                "Dry-run done. Candidate bot messages found: %d", total_candidates
            )
        else:
            logger.info("Cleanup done. Deleted bot messages: %d", total_deleted)

        await self.close()

    async def _cleanup_channel(self, channel_id: int) -> tuple[int, int]:
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception:
                logger.exception("Failed to fetch channel %s", channel_id)
                return 0, 0

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Channel %s is not a TextChannel (got %s), skipped.",
                channel_id,
                type(channel).__name__,
            )
            return 0, 0

        logger.info("Scanning channel %s (%s)", channel.name, channel.id)

        if self.dry_run:
            candidates = 0
            async for message in channel.history(limit=self.history_limit):
                if message.author.id != self.user.id:
                    continue
                candidates += 1
                if self.max_delete is not None and candidates >= self.max_delete:
                    break

            logger.info(
                "Channel %s done. candidates=%d deleted=0 dry_run=%s",
                channel.id,
                candidates,
                self.dry_run,
            )
            return 0, candidates

        matched_count = 0

        def check(message: discord.Message) -> bool:
            nonlocal matched_count
            if message.author.id != self.user.id:
                return False
            if self.max_delete is not None and matched_count >= self.max_delete:
                return False

            matched_count += 1
            return True

        try:
            deleted_messages = await channel.purge(
                limit=self.history_limit,
                check=check,
                bulk=True,
            )
            deleted = len(deleted_messages)
            candidates = matched_count
        except discord.Forbidden:
            logger.warning("No permission to purge channel %s", channel.id)
            return 0, 0
        except discord.HTTPException as exc:
            logger.warning("Failed to purge channel %s: %s", channel.id, exc)
            return 0, 0

        logger.info(
            "Channel %s done. candidates=%d deleted=%d dry_run=%s",
            channel.id,
            candidates,
            deleted,
            self.dry_run,
        )
        return deleted, candidates


if __name__ == "__main__":
    args = parse_args()
    client = CleanupClient(
        channel_ids=args.channel_ids,
        history_limit=args.limit,
        max_delete=args.max_delete,
        dry_run=args.dry_run,
    )
    client.run(TOKEN)
