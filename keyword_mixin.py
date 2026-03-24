import re
import time

import discord

from config import (
    DEFAULT_COOLDOWN,
    NOTIFICATION_MAX_DESCRIPTION_LENGTH,
    ZERO_WIDTH_SPACE,
    logger,
)


class KeywordMixin:
    def __init__(self):
        self._processing_messages: set[int] = set()

    def is_user_still_cooldown(self, uid: int, kw: str) -> bool:
        user_cooldown = self.cooldown_settings.get(uid, DEFAULT_COOLDOWN)
        last_time = self.last_notified.get((uid, kw), 0)

        return time.time() - last_time < user_cooldown

    async def send_notification(
        self, uid: int, message: discord.Message, kw: str
    ) -> None:
        target_user = await self.fetch_user(uid)

        image_urls: list[str] = []
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith(
                    "image/"
                ):
                    image_urls.append(attachment.url)
        if message.embeds:
            for embed_obj in message.embeds:
                if embed_obj.image and embed_obj.image.url:
                    image_urls.append(embed_obj.image.url)
                elif embed_obj.thumbnail and embed_obj.thumbnail.url:
                    image_urls.append(embed_obj.thumbnail.url)

        # Deduplicate while preserving order
        seen = set()
        image_urls = [u for u in image_urls if not (u in seen or seen.add(u))]

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

        embed.description = f"{message.content[:NOTIFICATION_MAX_DESCRIPTION_LENGTH]}{'...' if len(message.content) > NOTIFICATION_MAX_DESCRIPTION_LENGTH else ''}"

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
                nested = f"{nested[:NOTIFICATION_MAX_DESCRIPTION_LENGTH]}{'...' if len(nested) > NOTIFICATION_MAX_DESCRIPTION_LENGTH else ''}"
                embed.add_field(name=ZERO_WIDTH_SPACE, value=nested, inline=True)

        if not image_urls and author_icon_url:
            embed.set_thumbnail(url=author_icon_url)

        embed.add_field(
            name=ZERO_WIDTH_SPACE,
            value=f"[傳送門]({message.jump_url})",
            inline=False,
        )

        if image_urls:
            embed.set_image(url=image_urls[0])

        embed.set_footer(text=f"{server_name}﹥＃{channel_name}", icon_url=server_icon)

        extra_embeds: list[discord.Embed] = []
        for extra_url in image_urls[1:]:
            # set url as same as message jump url to make multi-image preview in one embed
            # and don't set other properties to avoid this trick failed
            extra = discord.Embed(color=0x3498DB, url=message.jump_url)
            extra.set_image(url=extra_url)
            extra_embeds.append(extra)

        try:
            if extra_embeds:
                await target_user.send(embeds=[embed, *extra_embeds])
            else:
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

        # Use ASCII word boundaries for keywords containing ASCII word chars.
        # This avoids matching substrings like 'pp' inside 'app', while still
        # matching '中文pp測試' and 'foo pp zoo'.
        if re.search(r"[A-Za-z0-9_]", kw):
            pattern = r"(?<![A-Za-z0-9_])" + re.escape(kw) + r"(?![A-Za-z0-9_])"
            return bool(re.search(pattern, string))

        return kw in string

    def is_trigger_keyword(self, message: discord.Message, kw: str) -> bool:
        if self.is_keyword_in_string(message.content, kw):
            return True

        for embed in message.embeds:
            if embed.title and self.is_keyword_in_string(embed.title, kw):
                return True
            if (
                embed.author
                and embed.author.name
                and self.is_keyword_in_string(embed.author.name, kw)
            ):
                return True
            if embed.description and self.is_keyword_in_string(embed.description, kw):
                return True
            for field in embed.fields:
                if self.is_keyword_in_string(
                    field.name, kw
                ) or self.is_keyword_in_string(field.value, kw):
                    return True
        return False

    async def check_and_notify(self, message: discord.Message) -> None:
        msg_id = message.id
        
        # Prevent race condition: if this message is already being processed, skip it
        if msg_id in self._processing_messages:
            logger.debug(f"Message {msg_id} is already being processed, skipping")
            return
        
        self._processing_messages.add(msg_id)
        try:
            for uid, keywords in self.keyword_cache.items():
                if message.author.id == uid:
                    continue

                if not await self.is_user_in_same_guild(uid, message):
                    continue
                
                notification_key = f"{message.id}:{uid}"
                if notification_key in self.notified_message_keywords:
                    continue

                for kw in keywords:
                    if not self.is_trigger_keyword(message, kw):
                        continue

                    if self.is_user_still_cooldown(uid, kw):
                        continue

                    try:
                        await self.send_notification(uid, message, kw)
                        self.update_last_notified(uid, kw)
                        self.notified_message_keywords.add(notification_key)
                        break
                    except Exception as e:
                        logger.exception(
                            "Exception occurred while notifying user %s: %s", uid, e
                        )

            if len(self.notified_message_keywords) > 5000:
                for _ in range(1000):
                    self.notified_message_keywords.pop()
        finally:
            self._processing_messages.discard(msg_id)

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
