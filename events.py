from bot import bot
from config import (
    HOLODEX_CHANNEL_IDS,
    HOLODEX_NOTIFY_LIVE_CHANNEL_ID,
    HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID,
    HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID,
    HOLODEX_ORG,
    HOLODEX_POLL_INTERVAL,
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
        bot.loop.create_task(bot.holodex_live_monitor())

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
    if bot.user in message.mentions:
        await bot.reply_when_mentioned(message)
        return

    await bot.check_and_notify(message)


@bot.event
async def on_message_edit(before, after):
    if len(before.embeds) == 0 and len(after.embeds) > 0:
        logger.debug(f"偵測到訊息產生預覽 Embed: {after.id}")
        await bot.check_and_notify(after)
    elif before.content != after.content:
        logger.debug(f"偵測到訊息改變: {after.id}")
        await bot.check_and_notify(after)
