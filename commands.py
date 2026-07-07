import sqlite3
import aiohttp
import discord
from discord import app_commands

from bot import bot
from config import (
    DEFAULT_COOLDOWN,
    logger,
    ADMIN_USER_IDS,
    RECORDING_OUTPUT_DIR,
)


@bot.tree.command(name="notify_cooldown", description="設定相同關鍵字通知的冷卻時間")
@app_commands.describe(seconds="冷卻時間（秒）")
async def notify_cooldown(interaction: discord.Interaction, seconds: int):
    await interaction.response.defer(ephemeral=True)

    if seconds < 0:
        try:
            await interaction.followup.send("秒數不能為負數！", ephemeral=True)
        except Exception as e:
            logger.exception(
                "Error sending cooldown error message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    uid = interaction.user.id
    conn = sqlite3.connect(bot.db_path)
    conn.execute(
        """
        INSERT INTO user_settings (user_id, seconds)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET seconds = excluded.seconds
        """,
        (uid, seconds),
    )
    conn.commit()
    conn.close()

    bot.cooldown_settings[uid] = seconds

    try:
        await interaction.followup.send(
            f"✅ 冷卻時間已設定為 `{seconds}` 秒。", ephemeral=True
        )
    except Exception as e:
        logger.exception(
            "Error sending cooldown confirmation to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) set cooldown to %d seconds", interaction.user, uid, seconds
    )


@bot.tree.command(name="notify_add", description="訂閱關鍵字通知")
@app_commands.describe(keyword="要訂閱的關鍵字（用 , 分隔）")
async def notify_add(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer(ephemeral=True)

    keywords = keyword.lower().strip().split(",")
    uid = interaction.user.id
    permission_verified = bot.has_permission_verified(uid)

    if not permission_verified:
        if not await bot.can_send_permission_test_message(interaction):
            logger.warning(
                "User %s(%d) failed permission verification", interaction.user, uid
            )
            return

        conn = sqlite3.connect(bot.db_path)
        original_seconds = bot.cooldown_settings.get(uid, DEFAULT_COOLDOWN)
        conn.execute(
            """
            INSERT INTO user_settings (user_id, permission_verified, seconds)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET permission_verified = excluded.permission_verified, seconds = excluded.seconds
            """,
            (uid, 1, original_seconds),
        )
        conn.commit()
        conn.close()

    conn = sqlite3.connect(bot.db_path)

    for kw in keywords:
        kw = kw.strip()
        if len(kw) == 0:
            continue

        res = conn.execute(
            "SELECT 1 FROM user_keywords WHERE user_id = ? AND keyword = ?", (uid, kw)
        ).fetchone()
        if res is not None:
            continue

        conn.execute("INSERT INTO user_keywords VALUES (?, ?)", (uid, kw))

    conn.commit()
    conn.close()

    if uid not in bot.keyword_cache:
        bot.keyword_cache[uid] = []
    for kw in keywords:
        if kw not in bot.keyword_cache[uid]:
            bot.keyword_cache[uid].append(kw)

    try:
        await interaction.followup.send(f"✅ 已訂閱：`{keyword}`", ephemeral=True)
    except Exception as e:
        logger.exception(
            "Error sending subscription confirmation to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) is subscribing to keyword: %s", interaction.user, uid, keyword
    )


@bot.tree.command(name="notify_list", description="查看所有訂閱關鍵字")
async def notify_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(bot.db_path)
    res = conn.execute(
        "SELECT keyword FROM user_keywords WHERE user_id = ?", (interaction.user.id,)
    )
    rows = res.fetchall()
    keywords = [r[0] for r in rows]
    msg = (
        "你訂閱的關鍵字：\n" + "\n".join(f"- {k}" for k in keywords)
        if keywords
        else "你還沒有訂閱任何關鍵字。"
    )

    try:
        await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        logger.exception(
            "Error sending keyword list to user %s(%d): %s",
            interaction.user,
            interaction.user.id,
            e,
        )

    logger.info(
        "User %s(%d) requested their keyword list",
        interaction.user,
        interaction.user.id,
    )


@bot.tree.command(name="notify_remove", description="取消訂閱關鍵字通知")
@app_commands.describe(keyword="要取消訂閱的關鍵字（用 , 分隔）")
async def notify_remove(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer(ephemeral=True)

    keywords = keyword.lower().strip().split(",")
    uid = interaction.user.id

    conn = sqlite3.connect(bot.db_path)
    for kw in keywords:
        kw = kw.strip()
        if len(kw) == 0:
            continue

        res = conn.execute(
            "SELECT 1 FROM user_keywords WHERE user_id = ? AND keyword = ?", (uid, kw)
        ).fetchone()

        if res is None:
            continue

        conn.execute(
            "DELETE FROM user_keywords WHERE user_id = ? AND keyword = ?", (uid, kw)
        )

        if uid in bot.keyword_cache and kw in bot.keyword_cache[uid]:
            bot.keyword_cache[uid].remove(kw)
    conn.commit()
    conn.close()

    try:
        await interaction.followup.send(f"✅ 已取消訂閱：`{keyword}`", ephemeral=True)
    except Exception as e:
        logger.exception(
            "Error sending unsubscription confirmation to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) is unsubscribing from keyword: %s", interaction.user, uid, keyword
    )


@bot.tree.command(name="emoji_stats", description="查看你最常使用的表情符號與次數")
@app_commands.describe(publish="是否將結果公開（預設 False，僅自己可見）")
async def emoji_stats(interaction: discord.Interaction, publish: bool = False):
    await interaction.response.defer(ephemeral=(not publish))

    uid = interaction.user.id
    conn = sqlite3.connect(bot.db_path)
    rows = conn.execute(
        """
        SELECT emoji, SUM(count) AS total
        FROM emoji_usage
        WHERE user_id = ?
        GROUP BY emoji
        ORDER BY total DESC
        LIMIT 10
        """,
        (uid,),
    ).fetchall()
    total_count = conn.execute(
        "SELECT COALESCE(SUM(count), 0) FROM emoji_usage WHERE user_id = ?",
        (uid,),
    ).fetchone()[0]
    conn.close()

    if not rows:
        try:
            await interaction.followup.send(
                "📊 你還沒有使用過任何表情符號！", ephemeral=(not publish)
            )
        except Exception as e:
            logger.exception(
                "Error sending emoji stats to user %s(%d): %s",
                interaction.user,
                uid,
                e,
            )
        return

    favorite_emoji, favorite_count = rows[0]
    description = ""
    for i, (emoji_str, count) in enumerate(rows, 1):
        description += f"{i}. {emoji_str} - {count} 次\n"

    embed = discord.Embed(
        title="📊 你的表情符號使用排行榜",
        description=description,
        color=0x3498DB,
        timestamp=interaction.created_at,
    )
    embed.add_field(
        name="⭐ 你最愛的表情符號",
        value=f"{favorite_emoji}（{favorite_count} 次）",
        inline=False,
    )
    embed.set_footer(text=f"總共使用 {total_count} 個表情符號")

    try:
        await interaction.followup.send(embed=embed, ephemeral=(not publish))
    except Exception as e:
        logger.exception(
            "Error sending emoji stats to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) requested their emoji statistics",
        interaction.user,
        uid,
    )


@bot.tree.command(
    name="emoji_rank", description="[管理員] 查看伺服器表情符號使用排行榜"
)
@app_commands.describe(
    top="顯示前幾名（1-25，預設 10）",
    by_user="改為顯示使用表情符號最多的成員排行（預設 False，顯示表情符號排行）",
    publish="是否將結果公開（預設 False，僅自己可見）",
)
async def emoji_rank(
    interaction: discord.Interaction,
    top: int = 10,
    by_user: bool = False,
    publish: bool = False,
):
    # Admin only
    if interaction.user.id not in ADMIN_USER_IDS:
        try:
            await interaction.response.send_message(
                "❌ 此命令僅限管理員使用！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending admin check message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    await interaction.response.defer(ephemeral=(not publish))

    if not interaction.guild:
        try:
            await interaction.followup.send(
                "❌ 此命令只能在伺服器中使用！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending guild-only error to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    top = max(1, min(top, 25))
    server_id = interaction.guild.id
    conn = sqlite3.connect(bot.db_path)
    if by_user:
        rows = conn.execute(
            """
            WITH user_totals AS (
                SELECT user_id, SUM(count) AS total_count
                FROM emoji_usage
                WHERE server_id = ?
                GROUP BY user_id
            ),
            emoji_totals AS (
                SELECT user_id, emoji, SUM(count) AS emoji_count
                FROM emoji_usage
                WHERE server_id = ?
                GROUP BY user_id, emoji
            ),
            ranked_emojis AS (
                SELECT user_id, emoji, emoji_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id
                           ORDER BY emoji_count DESC, emoji
                       ) AS rn
                FROM emoji_totals
            )
            SELECT u.user_id, u.total_count, t.emoji
            FROM user_totals u
            LEFT JOIN ranked_emojis t
              ON t.user_id = u.user_id
             AND t.rn = 1
            ORDER BY u.total_count DESC
            LIMIT ?
            """,
            (server_id, server_id, top),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT emoji, SUM(count) AS total_count
            FROM emoji_usage
            WHERE server_id = ?
            GROUP BY emoji
            ORDER BY total_count DESC, emoji
            LIMIT ?
            """,
            (server_id, top),
        ).fetchall()
    conn.close()

    if not rows:
        try:
            await interaction.followup.send(
                "📊 這個伺服器還沒有表情符號使用記錄！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending emoji rank to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    if by_user:
        description = ""
        for i, (user_id, count, top_emoji) in enumerate(rows, 1):
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"<@{user_id}>"
            emoji_suffix = f"，最常用：{top_emoji}" if top_emoji else ""
            description += f"{i}. {name} - {count} 次{emoji_suffix}\n"
        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} 表情符號使用者排行榜",
            description=description,
            color=0xE67E22,
            timestamp=interaction.created_at,
        )
        embed.set_footer(text="依每位成員使用表情符號的總次數排序")
    else:
        description = ""
        total_count = 0
        for i, (emoji_str, count) in enumerate(rows, 1):
            description += f"{i}. {emoji_str} - {count} 次\n"
            total_count += count
        embed = discord.Embed(
            title=f"📊 {interaction.guild.name} 表情符號使用排行榜",
            description=description,
            color=0x9B59B6,
            timestamp=interaction.created_at,
        )
        embed.set_footer(text=f"前 {len(rows)} 名共使用 {total_count} 個表情符號")

    try:
        await interaction.followup.send(embed=embed, ephemeral=(not publish))
    except Exception as e:
        logger.exception(
            "Error sending emoji rank to user %s(%d): %s",
            interaction.user,
            interaction.user.id,
            e,
        )

    logger.info(
        "Admin %s(%d) requested emoji rank (by_user=%s, top=%d) for guild %s(%d)",
        interaction.user,
        interaction.user.id,
        by_user,
        top,
        interaction.guild.name,
        interaction.guild.id,
    )


@bot.tree.command(
    name="emoji_received_stats",
    description="查看你收到最多的表情回應（reaction）與次數",
)
@app_commands.describe(
    publish="是否將結果公開（預設 False，僅自己可見）",
)
async def emoji_received_stats(interaction: discord.Interaction, publish: bool = False):
    await interaction.response.defer(ephemeral=(not publish))

    uid = interaction.user.id
    conn = sqlite3.connect(bot.db_path)
    # received_user_id = me: reactions others left on my messages.
    # user_id != me: exclude my own reactions on my own messages.
    rows = conn.execute(
        """
        SELECT emoji, SUM(count) AS total
        FROM emoji_usage
        WHERE received_user_id = ?
          AND user_id != ?
        GROUP BY emoji
        ORDER BY total DESC
        LIMIT 10
        """,
        (uid, uid),
    ).fetchall()
    total_count = conn.execute(
        "SELECT COALESCE(SUM(count), 0) FROM emoji_usage WHERE received_user_id = ? AND user_id != ?",
        (uid, uid),
    ).fetchone()[0]
    conn.close()

    if not rows:
        try:
            await interaction.followup.send(
                "📊 你還沒有收到任何表情回應！", ephemeral=(not publish)
            )
        except Exception as e:
            logger.exception(
                "Error sending emoji received stats to user %s(%d): %s",
                interaction.user,
                uid,
                e,
            )
        return

    favorite_emoji, favorite_count = rows[0]
    description = ""
    for i, (emoji_str, count) in enumerate(rows, 1):
        description += f"{i}. {emoji_str} - {count} 次\n"

    embed = discord.Embed(
        title="💝 你收到的表情回應排行榜",
        description=description,
        color=0xE91E63,
        timestamp=interaction.created_at,
    )
    embed.add_field(
        name="⭐ 你最常收到的表情符號",
        value=f"{favorite_emoji}（{favorite_count} 次）",
        inline=False,
    )
    embed.set_footer(text=f"總共收到 {total_count} 個表情回應")

    try:
        await interaction.followup.send(embed=embed, ephemeral=(not publish))
    except Exception as e:
        logger.exception(
            "Error sending emoji received stats to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )

    logger.info(
        "User %s(%d) requested their received emoji statistics",
        interaction.user,
        uid,
    )


@bot.tree.command(
    name="emoji_received_rank",
    description="[管理員] 查看本伺服器收到最多表情回應（reaction）的成員排行榜",
)
@app_commands.describe(
    top="顯示前幾名（1-25，預設 10）",
    publish="是否將結果公開（預設 False，僅自己可見）",
)
async def emoji_received_rank(
    interaction: discord.Interaction, top: int = 10, publish: bool = False
):
    # Admin only
    if interaction.user.id not in ADMIN_USER_IDS:
        try:
            await interaction.response.send_message(
                "❌ 此命令僅限管理員使用！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending admin check message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    await interaction.response.defer(ephemeral=(not publish))

    if not interaction.guild:
        try:
            await interaction.followup.send(
                "❌ 此命令只能在伺服器中使用！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending guild-only error to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    top = max(1, min(top, 25))
    server_id = interaction.guild.id

    conn = sqlite3.connect(bot.db_path)
    # received_user_id != 0 keeps reaction rows only (message content stores 0);
    # received_user_id != user_id ignores reactions on one's own message.
    rows = conn.execute(
        """
        WITH user_totals AS (
                SELECT received_user_id AS user_id, SUM(count) AS total_count
                FROM emoji_usage
                WHERE server_id = ?
                    AND received_user_id != 0
                    AND received_user_id != user_id
                GROUP BY received_user_id
        ),
        emoji_totals AS (
                SELECT received_user_id AS user_id, emoji, SUM(count) AS emoji_count
                FROM emoji_usage
                WHERE server_id = ?
                    AND received_user_id != 0
                    AND received_user_id != user_id
                GROUP BY received_user_id, emoji
        ),
        ranked_emojis AS (
                SELECT user_id, emoji, emoji_count,
                                ROW_NUMBER() OVER (
                                        PARTITION BY user_id
                                        ORDER BY emoji_count DESC, emoji
                                ) AS rn
                FROM emoji_totals
        )
        SELECT u.user_id, u.total_count, t.emoji
        FROM user_totals u
        LEFT JOIN ranked_emojis t
            ON t.user_id = u.user_id
            AND t.rn = 1
        ORDER BY u.total_count DESC
        LIMIT ?
        """,
        (server_id, server_id, top),
    ).fetchall()
    conn.close()

    if not rows:
        try:
            await interaction.followup.send(
                "📊 這個伺服器還沒有任何表情回應記錄！", ephemeral=(not publish)
            )
        except Exception as e:
            logger.exception(
                "Error sending emoji received rank to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    description = ""
    total_count = 0
    for i, (received_user_id, count, top_emoji) in enumerate(rows, 1):
        member = interaction.guild.get_member(received_user_id)
        name = member.display_name if member else f"<@{received_user_id}>"
        emoji_suffix = f"，最常收：{top_emoji}" if top_emoji else ""
        description += f"{i}. {name} - {count} 次{emoji_suffix}\n"
        total_count += count

    embed = discord.Embed(
        title=f"💝 {interaction.guild.name} 收到最多表情回應的成員",
        description=description,
        color=0xE91E63,
        timestamp=interaction.created_at,
    )
    embed.set_footer(text=f"前 {len(rows)} 名共收到 {total_count} 個表情回應")

    try:
        await interaction.followup.send(embed=embed, ephemeral=(not publish))
    except Exception as e:
        logger.exception(
            "Error sending emoji received rank to user %s(%d): %s",
            interaction.user,
            interaction.user.id,
            e,
        )

    logger.info(
        "Admin %s(%d) requested emoji received rank (top=%d) for guild %s(%d)",
        interaction.user,
        interaction.user.id,
        top,
        interaction.guild.name,
        interaction.guild.id,
    )


@bot.tree.command(name="clear_emoji_stats", description="[管理員] 清除表情符號統計資料")
async def clear_emoji_stats(interaction: discord.Interaction):
    # Check if user is admin
    if interaction.user.id not in ADMIN_USER_IDS:
        try:
            await interaction.response.send_message(
                "❌ 此命令僅限管理員使用！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending admin check message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        conn = sqlite3.connect(bot.db_path)
        conn.execute("DELETE FROM emoji_usage")
        conn.commit()
        conn.close()

        try:
            await interaction.followup.send(
                "✅ 已清除所有表情符號統計資料！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending clear confirmation to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )

        logger.info(
            "Admin %s(%d) cleared all emoji statistics",
            interaction.user,
            interaction.user.id,
        )

    except Exception as e:
        logger.exception(
            "Error clearing emoji stats by user %s(%d): %s",
            interaction.user,
            interaction.user.id,
            e,
        )
        try:
            await interaction.followup.send(
                f"⚠️ 清除資料時發生錯誤：{str(e)}", ephemeral=True
            )
        except Exception as e2:
            logger.exception(
                "Error sending clear error message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e2,
            )


@bot.tree.command(
    name="scan_emoji_history", description="[管理員] 掃描歷史訊息中的表情符號使用情況"
)
@app_commands.describe(
    channel="要掃描的頻道（若不指定且 scan_guild=False，則為當前頻道）",
    limit="每個頻道的掃描訊息數量上限（預設 1000，當 unlimited=True 時無效）",
    scan_guild="是否掃描整個伺服器（預設 False）",
    unlimited="是否不限制訊息數量（僅對 scan_guild=True 有效，預設 False）",
)
async def scan_emoji_history(
    interaction: discord.Interaction,
    channel: discord.TextChannel = None,
    limit: int = 1000,
    scan_guild: bool = False,
    unlimited: bool = False,
):
    # Check if user is admin
    if interaction.user.id not in ADMIN_USER_IDS:
        try:
            await interaction.response.send_message(
                "❌ 此命令僅限管理員使用！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending admin check message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    await interaction.response.defer(ephemeral=True)

    # Validate limit (only when not unlimited)
    if not unlimited and (limit <= 0 or limit > 10000):
        try:
            await interaction.followup.send(
                "❌ 訊息數量限制必須在 1-10000 之間！", ephemeral=True
            )
        except Exception as e:
            logger.exception(
                "Error sending limit validation error to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e,
            )
        return

    try:
        if scan_guild:
            # Scan entire guild
            if not interaction.guild:
                try:
                    await interaction.followup.send(
                        "❌ 此命令只能在伺服器中使用！", ephemeral=True
                    )
                except Exception as e:
                    logger.exception(
                        "Error sending guild-only error to user %s(%d): %s",
                        interaction.user,
                        interaction.user.id,
                        e,
                    )
                return

            # Send initial progress message
            if unlimited:
                progress_msg = await interaction.followup.send(
                    f"🔍 開始掃描伺服器 `{interaction.guild.name}` 的所有文字頻道...\n"
                    f"訊息數量：無限制（掃描所有歷史訊息）\n"
                    f"⚠️ 此操作可能需要較長時間，請耐心等待...\n"
                    f"請稍候...",
                    ephemeral=True,
                )
            else:
                progress_msg = await interaction.followup.send(
                    f"🔍 開始掃描伺服器 `{interaction.guild.name}` 的所有文字頻道...\n"
                    f"每個頻道訊息上限：{limit}\n"
                    f"請稍候...",
                    ephemeral=True,
                )

            # Perform the guild scan
            messages_scanned, emojis_found, channels_scanned = (
                await bot.scan_guild_history(interaction.guild, limit, unlimited)
            )

            # Update progress message with results
            embed = discord.Embed(
                title="✅ 伺服器歷史訊息掃描完成",
                color=0x2ECC71,
                timestamp=interaction.created_at,
            )

            embed.add_field(
                name="📊 掃描結果",
                value=f"伺服器：{interaction.guild.name}\n"
                f"掃描頻道：{channels_scanned}\n"
                f"掃描訊息：{messages_scanned}\n"
                f"發現表情符號：{emojis_found}",
                inline=False,
            )

            if messages_scanned > 0:
                avg_emojis = emojis_found / messages_scanned
                embed.add_field(
                    name="📈 統計資訊",
                    value=f"平均每訊息表情符號：{avg_emojis:.2f}",
                    inline=True,
                )

            if channels_scanned > 0:
                avg_channels = messages_scanned / channels_scanned
                embed.add_field(
                    name="📈 頻道統計",
                    value=f"平均每頻道訊息：{avg_channels:.1f}",
                    inline=True,
                )

            await progress_msg.edit(content=None, embed=embed)

            logger.info(
                "Admin %s(%d) scanned guild %s(%d): %d channels, %d messages, %d emojis found",
                interaction.user,
                interaction.user.id,
                interaction.guild.name,
                interaction.guild.id,
                channels_scanned,
                messages_scanned,
                emojis_found,
            )

        else:
            # Scan single channel (existing logic)
            target_channel = channel or interaction.channel

            if not isinstance(target_channel, discord.TextChannel):
                try:
                    await interaction.followup.send(
                        "❌ 只能掃描文字頻道！", ephemeral=True
                    )
                except Exception as e:
                    logger.exception(
                        "Error sending channel type error to user %s(%d): %s",
                        interaction.user,
                        interaction.user.id,
                        e,
                    )
                return

            # Send initial progress message
            progress_msg = await interaction.followup.send(
                f"🔍 開始掃描頻道 `{target_channel.name}` 的歷史訊息...\n"
                f"目標訊息數量：{limit}\n"
                f"請稍候...",
                ephemeral=True,
            )

            # Perform the scan
            messages_scanned, emojis_found = await bot.scan_channel_history(
                target_channel, limit
            )

            # Update progress message with results
            embed = discord.Embed(
                title="✅ 頻道歷史訊息掃描完成",
                color=0x2ECC71,
                timestamp=interaction.created_at,
            )

            embed.add_field(
                name="📊 掃描結果",
                value=f"頻道：#{target_channel.name}\n"
                f"掃描訊息：{messages_scanned}\n"
                f"發現表情符號：{emojis_found}",
                inline=False,
            )

            if messages_scanned > 0:
                avg_emojis = emojis_found / messages_scanned
                embed.add_field(
                    name="📈 統計資訊",
                    value=f"平均每訊息表情符號：{avg_emojis:.2f}",
                    inline=True,
                )

            await progress_msg.edit(content=None, embed=embed)

            logger.info(
                "Admin %s(%d) scanned channel %s(%d): %d messages, %d emojis found",
                interaction.user,
                interaction.user.id,
                target_channel.name,
                target_channel.id,
                messages_scanned,
                emojis_found,
            )

    except Exception as e:
        logger.exception(
            "Error during emoji history scan by user %s(%d): %s",
            interaction.user,
            interaction.user.id,
            e,
        )
        try:
            await interaction.followup.send(
                f"⚠️ 掃描過程中發生錯誤：{str(e)}", ephemeral=True
            )
        except Exception as e2:
            logger.exception(
                "Error sending scan error message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e2,
            )


@bot.tree.command(
    name="verify_membership",
    description="連結 YouTube 帳號以驗證頻道會員資格並取得會員身分組",
)
async def verify_membership(interaction: discord.Interaction):
    if not bot.membership_enabled:
        await interaction.response.send_message(
            "❌ 此伺服器尚未啟用 YouTube 會員驗證功能。", ephemeral=True
        )
        return

    url = bot.build_oauth_url(interaction.user.id)
    embed = discord.Embed(
        title="🔗 YouTube 會員驗證",
        description=(
            "點擊下方連結，使用你的 Google 帳號授權，"
            "Bot 會確認你是否為指定頻道的會員並自動給予身分組。\n\n"
            f"**[點此開始授權]({url})**\n\n"
            "⚠️ 此連結僅限你本人使用、15 分鐘內有效，請勿分享。"
        ),
        color=0xE74C3C,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info(
        "User %s(%d) started membership verification",
        interaction.user,
        interaction.user.id,
    )


@bot.tree.command(name="membership_status", description="查看你的 YouTube 會員驗證狀態")
async def membership_status(interaction: discord.Interaction):
    if not bot.membership_enabled:
        await interaction.response.send_message(
            "❌ 此伺服器尚未啟用 YouTube 會員驗證功能。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    row = bot.get_membership_row(interaction.user.id)
    if not row:
        await interaction.followup.send(
            "你尚未連結 YouTube 帳號，請使用 `/verify_membership`。", ephemeral=True
        )
        return

    _, _yt_channel_id, _, last_checked = row
    checked = f"<t:{last_checked}:R>" if last_checked else "尚未檢查"

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send(
            "請在伺服器中使用此指令以查看該伺服器的驗證狀態。", ephemeral=True
        )
        return

    mappings = [
        (ch, role_id)
        for gid, ch, role_id in bot.membership_channel_map
        if gid == guild.id
    ]
    if not mappings:
        await interaction.followup.send(
            f"已連結你的 YouTube 帳號，但本伺服器尚未設定任何驗證頻道。\n最後檢查：{checked}",
            ephemeral=True,
        )
        return

    # Report per-channel status from the member's actual roles (source of truth).
    member = guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await guild.fetch_member(interaction.user.id)
        except Exception:
            member = None

    lines = []
    for ch, role_id in mappings:
        has_role = bool(member) and any(r.id == role_id for r in member.roles)
        role = guild.get_role(role_id)
        role_name = role.name if role else f"@{role_id}"
        lines.append(f"{'✅' if has_role else '❌'} `{ch}` → {role_name}")

    await interaction.followup.send(
        "你在本伺服器的 YouTube 會員驗證狀態：\n"
        + "\n".join(lines)
        + f"\n最後檢查：{checked}",
        ephemeral=True,
    )


@bot.tree.command(
    name="membership_unlink", description="解除 YouTube 帳號連結並移除會員身分組"
)
async def membership_unlink(interaction: discord.Interaction):
    if not bot.membership_enabled:
        await interaction.response.send_message(
            "❌ 此伺服器尚未啟用 YouTube 會員驗證功能。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    removed = await bot.unlink_membership(interaction.user.id)
    if removed:
        await interaction.followup.send(
            "✅ 已解除連結並撤銷授權，會員身分組已移除。", ephemeral=True
        )
    else:
        await interaction.followup.send(
            "你尚未連結任何 YouTube 帳號。", ephemeral=True
        )
    logger.info(
        "User %s(%d) unlinked membership", interaction.user, interaction.user.id
    )


@bot.tree.command(
    name="membership_recheck",
    description="[管理員] 立即重新驗證所有已連結成員的會員資格",
)
async def membership_recheck(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return
    if not bot.membership_enabled:
        await interaction.response.send_message(
            "❌ 此伺服器尚未啟用 YouTube 會員驗證功能。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    bot.loop.create_task(bot.membership_recheck_all())
    await interaction.followup.send(
        "🔄 已在背景開始重新驗證所有已連結成員。", ephemeral=True
    )
    logger.info(
        "Admin %s(%d) triggered membership recheck",
        interaction.user,
        interaction.user.id,
    )


@bot.tree.command(
    name="membership_add",
    description="[管理員] 新增／更新 YouTube 頻道與會員身分組的對應",
)
@app_commands.describe(
    channel_id="YouTube 頻道 ID（UC 開頭）",
    role="驗證通過後要授予的身分組",
)
async def membership_add(
    interaction: discord.Interaction, channel_id: str, role: discord.Role
):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return
    if not bot.membership_enabled:
        await interaction.response.send_message(
            "❌ 此伺服器尚未啟用 YouTube 會員驗證功能。", ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ 請在伺服器中使用此指令。", ephemeral=True
        )
        return

    channel_id = channel_id.strip()
    if not channel_id.startswith("UC") or len(channel_id) < 20:
        await interaction.response.send_message(
            "❌ 頻道 ID 格式錯誤，需為 `UC` 開頭的頻道 ID。", ephemeral=True
        )
        return
    if role.guild.id != interaction.guild.id:
        await interaction.response.send_message(
            "❌ 該身分組不屬於這個伺服器。", ephemeral=True
        )
        return
    if role.is_default() or role.managed:
        await interaction.response.send_message(
            "❌ 不能使用 @everyone 或由整合管理的身分組。", ephemeral=True
        )
        return

    bot.add_membership_channel(
        interaction.guild.id, channel_id, role.id, interaction.user.id
    )
    await interaction.response.send_message(
        f"✅ 已在本伺服器設定：`{channel_id}` → {role.mention}\n"
        "新設定會在下次自動重新驗證時套用，或使用 `/membership_recheck` 立即套用。",
        ephemeral=True,
    )
    logger.info(
        "Admin %s(%d) mapped channel %s -> role %d",
        interaction.user,
        interaction.user.id,
        channel_id,
        role.id,
    )


@bot.tree.command(
    name="membership_remove",
    description="[管理員] 移除 YouTube 頻道與會員身分組的對應",
)
@app_commands.describe(channel_id="要移除的 YouTube 頻道 ID（UC 開頭）")
async def membership_remove(interaction: discord.Interaction, channel_id: str):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ 請在伺服器中使用此指令。", ephemeral=True
        )
        return

    removed = bot.remove_membership_channel(interaction.guild.id, channel_id.strip())
    if removed:
        await interaction.response.send_message(
            f"✅ 已移除本伺服器頻道 `{channel_id.strip()}` 的對應。"
            "（現有成員的身分組會在下次重新驗證時同步。）",
            ephemeral=True,
        )
        logger.info(
            "Admin %s(%d) removed channel mapping %s",
            interaction.user,
            interaction.user.id,
            channel_id.strip(),
        )
    else:
        await interaction.response.send_message(
            f"找不到頻道 `{channel_id.strip()}` 的對應。", ephemeral=True
        )


@bot.tree.command(
    name="membership_list", description="[管理員] 列出所有 YouTube 頻道與身分組對應"
)
async def membership_list(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ 請在伺服器中使用此指令。", ephemeral=True
        )
        return

    guild = interaction.guild
    mappings = [
        (ch, role_id)
        for gid, ch, role_id in bot.membership_channel_map
        if gid == guild.id
    ]
    if not mappings:
        await interaction.response.send_message(
            "本伺服器尚未設定任何頻道對應，使用 `/membership_add` 新增。", ephemeral=True
        )
        return

    lines = []
    for ch, role_id in mappings:
        role = guild.get_role(role_id)
        lines.append(f"- `{ch}` → {role.mention if role else f'@{role_id}（找不到）'}")
    await interaction.response.send_message(
        "目前的會員驗證頻道對應：\n" + "\n".join(lines), ephemeral=True
    )


@bot.tree.command(
    name="membership_account",
    description="查看你目前連結的 YouTube 帳號",
)
async def membership_account(interaction: discord.Interaction):
    if not bot.membership_enabled:
        await interaction.response.send_message(
            "❌ 此伺服器尚未啟用 YouTube 會員驗證功能。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    row = bot.get_membership_row(interaction.user.id)
    if not row:
        await interaction.followup.send(
            "你尚未連結 YouTube 帳號，請使用 `/verify_membership`。", ephemeral=True
        )
        return

    _, yt_channel_id, _, last_checked = row
    checked = f"<t:{last_checked}:R>" if last_checked else "尚未檢查"

    if yt_channel_id:
        url = f"https://www.youtube.com/channel/{yt_channel_id}"
        title = None
        try:
            async with aiohttp.ClientSession() as session:
                title = await bot.get_channel_title(session, yt_channel_id)
        except Exception:
            logger.debug("Failed to fetch channel title", exc_info=True)
        name_line = f"[{title}]({url})" if title else url
        description = (
            f"目前連結的 YouTube 頻道：\n{name_line}\n"
            f"頻道 ID：`{yt_channel_id}`\n"
            f"最後檢查：{checked}"
        )
    else:
        description = (
            "已連結你的 YouTube 帳號，但沒有取得頻道 ID"
            "（授權時可能未包含頻道資訊）。\n"
            f"最後檢查：{checked}"
        )

    embed = discord.Embed(
        title="🔗 已連結的 YouTube 帳號",
        description=description,
        color=0xE74C3C,
    )
    embed.set_footer(text="使用 /verify_membership 重新連結，/membership_unlink 解除連結")
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info(
        "User %s(%d) viewed their linked YouTube account",
        interaction.user,
        interaction.user.id,
    )


@bot.tree.command(
    name="membership_role_list",
    description="[管理員] 列出本伺服器各會員身分組目前持有的成員",
)
async def membership_role_list(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ 請在伺服器中使用此指令。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    mappings = [
        (ch, role_id)
        for gid, ch, role_id in bot.membership_channel_map
        if gid == guild.id
    ]
    if not mappings:
        await interaction.followup.send(
            "本伺服器尚未設定任何頻道對應，使用 `/membership_add` 新增。",
            ephemeral=True,
        )
        return

    # Annotate each holder with their linked YouTube channel id, if known.
    linked = {uid: yt for uid, yt, _enc, _last in bot._all_membership_rows()}

    embed = discord.Embed(
        title=f"🎫 {guild.name} 會員身分組名單",
        color=0x3498DB,
        timestamp=interaction.created_at,
    )
    total = 0
    for ch, role_id in mappings:
        role = guild.get_role(role_id)
        if role is None:
            embed.add_field(
                name=f"`{ch}`", value=f"@{role_id}（找不到身分組）", inline=False
            )
            continue

        holders = role.members  # requires the member cache (warmed on_ready)
        total += len(holders)
        if holders:
            lines = []
            for member in holders[:20]:
                yt = linked.get(member.id)
                suffix = f"（`{yt}`）" if yt else "（未連結）"
                lines.append(f"• {member.mention} {suffix}")
            if len(holders) > 20:
                lines.append(f"…以及其他 {len(holders) - 20} 位")
            value = "\n".join(lines)
        else:
            value = "（目前沒有成員持有此身分組）"

        embed.add_field(
            name=f"{role.name} ← `{ch}`（{len(holders)} 人）",
            value=value[:1024],
            inline=False,
        )

    embed.set_footer(text=f"共 {total} 個身分組持有記錄（依實際 Discord 身分組計算）")
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info(
        "Admin %s(%d) listed membership role holders for guild %s(%d)",
        interaction.user,
        interaction.user.id,
        guild.name,
        guild.id,
    )


@bot.tree.command(
    name="record_start",
    description="[管理員] 開始錄製 YouTube 直播",
)
@app_commands.describe(
    target="YouTube 影片網址或影片 ID（進行中的直播）",
    from_start="是否從直播開頭錄製（預設 False，從現在開始）",
)
async def record_start(
    interaction: discord.Interaction, target: str, from_start: bool = False
):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    ok, message, rec = await bot.start_recording(
        target,
        from_start,
        interaction.user.id,
        interaction.guild.id if interaction.guild else None,
    )
    if not ok:
        await interaction.followup.send(
            "❌ " + (message or "無法開始錄影。"), ephemeral=True
        )
        return

    await interaction.followup.send(
        f"🔴 已開始錄製 `{rec['key']}`"
        + ("（從直播開頭）" if from_start else "（從現在開始）")
        + f"\n輸出目錄：`{RECORDING_OUTPUT_DIR}`\n"
        f"使用 `/record_list` 查看進度，`/record_stop {rec['key']}` 停止。",
        ephemeral=True,
    )
    logger.info(
        "Admin %s(%d) started recording %s (from_start=%s)",
        interaction.user,
        interaction.user.id,
        rec["key"],
        from_start,
    )


@bot.tree.command(
    name="record_stop",
    description="[管理員] 停止指定的 YouTube 直播錄影",
)
@app_commands.describe(recording_id="要停止的錄影 ID（見 /record_list）")
async def record_stop(interaction: discord.Interaction, recording_id: str):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    stopped = await bot.stop_recording(recording_id.strip())
    if stopped:
        await interaction.followup.send(
            f"⏹️ 已停止錄影 `{recording_id.strip()}`，檔案已儲存至 "
            f"`{RECORDING_OUTPUT_DIR}`。",
            ephemeral=True,
        )
        logger.info(
            "Admin %s(%d) stopped recording %s",
            interaction.user,
            interaction.user.id,
            recording_id.strip(),
        )
    else:
        await interaction.followup.send(
            f"找不到進行中的錄影 `{recording_id.strip()}`（用 `/record_list` 確認）。",
            ephemeral=True,
        )


@bot.tree.command(
    name="record_list",
    description="[管理員] 列出進行中的 YouTube 直播錄影",
)
async def record_list(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message(
            "❌ 此命令僅限管理員使用！", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    recordings = bot.list_recordings()
    if not recordings:
        await interaction.followup.send("目前沒有進行中的錄影。", ephemeral=True)
        return

    lines = []
    for rec in recordings:
        started = rec.get("started_at")
        when = f"<t:{started}:R>" if started else "?"
        scope = "從開頭" if rec.get("from_start") else "從現在"
        lines.append(
            f"🔴 `{rec['key']}` — 開始於 {when}（{scope}）\n"
            f"　　{rec.get('url', '')}"
        )

    embed = discord.Embed(
        title="🔴 進行中的直播錄影",
        description="\n".join(lines)[:4000],
        color=0xE74C3C,
        timestamp=interaction.created_at,
    )
    embed.set_footer(text=f"共 {len(recordings)} 個 · 用 /record_stop <ID> 停止")
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info(
        "Admin %s(%d) listed %d active recording(s)",
        interaction.user,
        interaction.user.id,
        len(recordings),
    )
