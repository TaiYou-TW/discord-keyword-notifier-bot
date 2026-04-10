import re
import sqlite3
import time

import discord

from config import (
    DEFAULT_COOLDOWN,
    NOTIFICATION_MAX_DESCRIPTION_LENGTH,
    ZERO_WIDTH_SPACE,
    logger,
)


class ChannelMuteView(discord.ui.View):
    def __init__(
        self,
        channel_id: int,
        channel_name: str,
        guild_name: str,
        jump_url: str,
        muted: bool = False,
        timeout: float = 86400,
    ):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.guild_name = guild_name
        self.jump_url = jump_url
        self.muted = muted
        self.add_item(
            discord.ui.Button(
                label="傳送門",
                style=discord.ButtonStyle.link,
                url=self.jump_url,
                emoji="🔗",
            )
        )

        if self.muted:
            unmute_button = discord.ui.Button(
                label="解除此頻道退訂",
                style=discord.ButtonStyle.secondary,
                emoji="🔔",
            )

            async def unmute_callback(interaction: discord.Interaction) -> None:
                bot = interaction.client
                if bot is None or not hasattr(bot, "unmute_channel_for_user"):
                    await interaction.response.send_message(
                        "目前無法處理這個操作。", ephemeral=True
                    )
                    return

                if bot.unmute_channel_for_user(interaction.user.id, self.channel_id):
                    await interaction.response.edit_message(
                        view=ChannelMuteView(
                            channel_id=self.channel_id,
                            channel_name=self.channel_name,
                            guild_name=self.guild_name,
                            jump_url=self.jump_url,
                            muted=False,
                        )
                    )
                    await interaction.followup.send(
                        f"✅ 已恢復訂閱：{self.guild_name}﹥＃{self.channel_name}",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"ℹ️ 這個頻道目前沒有被退訂：{self.guild_name}﹥＃{self.channel_name}",
                        ephemeral=True,
                    )

            unmute_button.callback = unmute_callback
            self.add_item(unmute_button)
        else:
            mute_button = discord.ui.Button(
                label="取消訂閱此頻道",
                style=discord.ButtonStyle.danger,
                emoji="🔕",
            )

            async def mute_callback(interaction: discord.Interaction) -> None:
                bot = interaction.client
                if bot is None or not hasattr(bot, "mute_channel_for_user"):
                    await interaction.response.send_message(
                        "目前無法處理這個操作。", ephemeral=True
                    )
                    return

                if bot.mute_channel_for_user(interaction.user.id, self.channel_id):
                    await interaction.response.edit_message(
                        view=ChannelMuteView(
                            channel_id=self.channel_id,
                            channel_name=self.channel_name,
                            guild_name=self.guild_name,
                            jump_url=self.jump_url,
                            muted=True,
                        )
                    )
                    await interaction.followup.send(
                        f"✅ 已取消訂閱：{self.guild_name}﹥＃{self.channel_name}",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"ℹ️ 你已經取消訂閱過：{self.guild_name}﹥＃{self.channel_name}",
                        ephemeral=True,
                    )

            mute_button.callback = mute_callback
            self.add_item(mute_button)


class KeywordMixin:
    def __init__(self, **kwargs):
        self._processing_messages: set[int] = set()
        super().__init__(**kwargs)

    def is_user_still_cooldown(self, uid: int, kw: str) -> bool:
        user_cooldown = self.cooldown_settings.get(uid, DEFAULT_COOLDOWN)
        last_time = self.last_notified.get((uid, kw), 0)

        return time.time() - last_time < user_cooldown

    def load_muted_channels(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT user_id, channel_id FROM muted_channels"
            ).fetchall()

        for uid, channel_id in rows:
            self.muted_channel_ids.setdefault(uid, set()).add(channel_id)

        logger.info("Loaded muted channels for %d users.", len(self.muted_channel_ids))

    def is_channel_muted(self, uid: int, channel_id: int) -> bool:
        return channel_id in self.muted_channel_ids.get(uid, set())

    def mute_channel_for_user(self, uid: int, channel_id: int) -> bool:
        muted_channels = self.muted_channel_ids.setdefault(uid, set())
        if channel_id in muted_channels:
            return False

        muted_channels.add(channel_id)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO muted_channels (user_id, channel_id) VALUES (?, ?)",
                    (uid, channel_id),
                )
        except Exception:
            logger.exception("Failed to save muted channel for user %s", uid)

        return True

    def unmute_channel_for_user(self, uid: int, channel_id: int) -> bool:
        muted_channels = self.muted_channel_ids.get(uid)
        if muted_channels is None or channel_id not in muted_channels:
            return False

        muted_channels.remove(channel_id)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM muted_channels WHERE user_id = ? AND channel_id = ?",
                    (uid, channel_id),
                )
        except Exception:
            logger.exception("Failed to remove muted channel for user %s", uid)

        return True

    async def send_notification(
        self, uid: int, message: discord.Message, kw: str
    ) -> None:
        target_user = await self.fetch_user(uid)

        # Check if original message has spoiler images, ignore them
        image_urls: list[str] = []
        if message.attachments:
            for attachment in message.attachments:
                if (
                    attachment.content_type
                    and attachment.content_type.startswith("image/")
                    and not attachment.spoiler
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

        if image_urls:
            embed.set_image(url=image_urls[0])

        embed.set_footer(text=f"{server_name}﹥＃{channel_name}", icon_url=server_icon)

        view = ChannelMuteView(
            channel_id=message.channel.id,
            channel_name=channel_name,
            guild_name=server_name,
            jump_url=message.jump_url,
            muted=self.is_channel_muted(uid, message.channel.id),
        )

        extra_embeds: list[discord.Embed] = []
        for extra_url in image_urls[1:]:
            # set url as same as message jump url to make multi-image preview in one embed
            # and don't set other properties to avoid this trick failed
            extra = discord.Embed(color=0x3498DB, url=message.jump_url)
            extra.set_image(url=extra_url)
            extra_embeds.append(extra)

        try:
            if extra_embeds:
                await target_user.send(embeds=[embed, *extra_embeds], view=view)
            else:
                await target_user.send(embed=embed, view=view)
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

                if message.guild and self.is_channel_muted(uid, message.channel.id):
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
