from bot import bot
from config import (
    HOLODEX_CHANNEL_IDS,
    HOLODEX_NOTIFY_LIVE_CHANNEL_ID,
    HOLODEX_NOTIFY_UPCOMING_CHANNEL_ID,
    HOLODEX_NOTIFY_UPLOAD_CHANNEL_ID,
    HOLODEX_ORG,
    HOLODEX_POLL_INTERVAL,
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

    if message.author == bot.user:
        return

    await bot.check_and_notify(message)


@bot.event
async def on_message_edit(before, after):
    if after.author == bot.user:
        return

    if len(before.embeds) == 0 and len(after.embeds) > 0:
        logger.debug(f"偵測到訊息產生預覽 Embed: {after.id}")
        await bot.check_and_notify(after)
    elif before.content != after.content:
        logger.debug(f"偵測到訊息改變: {after.id}")
        await bot.check_and_notify(after)
