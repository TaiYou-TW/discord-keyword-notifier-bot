from bot import bot
from config import (
    HOLODEX_CHANNEL_IDS,
    HOLODEX_NOTIFY_LIVE_CHANNEL_ID,
    HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID,
    HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID,
    HOLODEX_ORG,
    HOLODEX_POLL_INTERVAL,
    MEMBERSHIP_CHECK_INTERVAL,
    RECORDING_RETENTION_DAYS,
    TWITTER_NOTIFY_CHANNEL_ID,
    TWITTER_POLL_INTERVAL,
    TWITTER_SCREEN_NAMES,
    YT_CHANNEL_IDS,
    YT_NOTIFY_CHANNEL_ID,
    YT_POLL_INTERVAL,
    logger,
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
        if bot.holodex_monitor_task is None or bot.holodex_monitor_task.done():
            bot.holodex_monitor_task = bot.loop.create_task(
                bot.holodex_live_monitor()
            )

    if TWITTER_SCREEN_NAMES and TWITTER_NOTIFY_CHANNEL_ID:
        logger.info(
            "Starting Twitter profile monitor for accounts: %s (interval %ds)",
            TWITTER_SCREEN_NAMES,
            TWITTER_POLL_INTERVAL,
        )
        if bot.twitter_monitor_task is None or bot.twitter_monitor_task.done():
            bot.twitter_monitor_task = bot.loop.create_task(
                bot.twitter_profile_monitor()
            )

    if YT_CHANNEL_IDS and YT_NOTIFY_CHANNEL_ID:
        logger.info(
            "Starting YT community monitor for channels: %s (interval %ds)",
            YT_CHANNEL_IDS,
            YT_POLL_INTERVAL,
        )
        if (
            bot.yt_community_monitor_task is None
            or bot.yt_community_monitor_task.done()
        ):
            bot.yt_community_monitor_task = bot.loop.create_task(
                bot.youtube_community_monitor()
            )

    if bot.membership_enabled:
        logger.info(
            "Starting YouTube membership verification (re-check every %ds)",
            MEMBERSHIP_CHECK_INTERVAL,
        )
        await bot.start_membership_server()
        if (
            bot.membership_monitor_task is None
            or bot.membership_monitor_task.done()
        ):
            bot.membership_monitor_task = bot.loop.create_task(
                bot.membership_monitor()
            )

    if RECORDING_RETENTION_DAYS > 0:
        logger.info(
            "Starting recording retention cleanup (delete files older than %d day(s))",
            RECORDING_RETENTION_DAYS,
        )
        if (
            bot.recording_cleanup_task is None
            or bot.recording_cleanup_task.done()
        ):
            bot.recording_cleanup_task = bot.loop.create_task(
                bot.recording_cleanup_monitor()
            )


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
    # Record emoji usage for every message, regardless of the branches below.
    bot.loop.create_task(bot.record_message_emojis(message))
    if bot.user in message.mentions:
        await bot.reply_when_mentioned(message)
        return

    await bot.check_and_notify(message)


@bot.event
async def on_raw_reaction_add(payload):
    # Record emoji usage when a user reacts. The raw event fires even for
    # messages that aren't in the bot's cache, so old messages are covered too.
    if payload.user_id == bot.user.id:
        return
    # payload.member is populated for guild reactions; skip other bots.
    if payload.member is not None and payload.member.bot:
        return

    server_id = payload.guild_id or 0

    # The reaction recipient is the reacted message's author. message_author_id
    # is filled in for free when the message is cached; otherwise fetch it.
    received_user_id = payload.message_author_id or 0
    if not received_user_id:
        channel = bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(payload.channel_id)
            except Exception:
                channel = None
        if channel is not None:
            try:
                message = await channel.fetch_message(payload.message_id)
                if not message.author.bot:
                    received_user_id = message.author.id
            except Exception:
                received_user_id = 0

    # Don't attribute received reactions to the bot's own messages.
    if received_user_id == bot.user.id:
        received_user_id = 0

    await bot.record_emoji_usage(
        payload.user_id, str(payload.emoji), received_user_id, server_id
    )


@bot.event
async def on_message_edit(before, after):
    if len(before.embeds) == 0 and len(after.embeds) > 0:
        logger.debug(f"偵測到訊息產生預覽 Embed: {after.id}")
        await bot.check_and_notify(after)
    elif before.content != after.content:
        logger.debug(f"偵測到訊息改變: {after.id}")
        await bot.check_and_notify(after)
