import sqlite3
import discord
from discord import app_commands

from bot import bot
from config import DEFAULT_COOLDOWN, logger, ADMIN_USER_IDS


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


@bot.tree.command(name="emoji_stats", description="查看表情符號使用排行榜")
@app_commands.describe(guild_stats="是否查看全伺服器統計（預設 False，查看個人統計）")
async def emoji_stats(interaction: discord.Interaction, guild_stats: bool = False):
    await interaction.response.defer(ephemeral=True)

    uid = interaction.user.id
    conn = sqlite3.connect(bot.db_path)
    
    if guild_stats:
        # Check if user is in a guild
        if not interaction.guild:
            try:
                await interaction.followup.send(
                    "❌ 此功能只能在伺服器中使用！", 
                    ephemeral=True
                )
            except Exception as e:
                logger.exception(
                    "Error sending guild-only error to user %s(%d): %s",
                    interaction.user,
                    uid,
                    e,
                )
            return

        # Get guild member IDs from memory cache
        member_ids = bot.guild_member_ids.get(interaction.guild.id, set())
        if not member_ids:
            # If cache is empty, try to get from guild
            if interaction.guild.chunked:
                member_ids = {member.id for member in interaction.guild.members}
            else:
                try:
                    await interaction.followup.send(
                        "❌ 無法獲取伺服器成員列表，請稍後再試！", 
                        ephemeral=True
                    )
                except Exception as e:
                    logger.exception(
                        "Error sending member list error to user %s(%d): %s",
                        interaction.user,
                        uid,
                        e,
                    )
                return

        # Get top 10 most used emojis across the entire guild
        placeholders = ','.join('?' for _ in member_ids)
        res = conn.execute(
            f"""
            SELECT emoji, SUM(count) as total_count 
            FROM emoji_usage 
            WHERE user_id IN ({placeholders})
            GROUP BY emoji 
            ORDER BY total_count DESC 
            LIMIT 10
            """, 
            tuple(member_ids)
        )
        rows = res.fetchall()
        
        if not rows:
            try:
                await interaction.followup.send(
                    "📊 這個伺服器還沒有表情符號使用記錄！", 
                    ephemeral=True
                )
            except Exception as e:
                logger.exception(
                    "Error sending guild emoji stats to user %s(%d): %s",
                    interaction.user,
                    uid,
                    e,
                )
            return

        # Create embed with guild emoji statistics
        embed = discord.Embed(
            title=f"📊 {interaction.guild.name} 表情符號使用排行榜",
            color=0x9B59B6,
            timestamp=interaction.created_at
        )

        description = ""
        total_count = 0
        
        for i, (emoji, count) in enumerate(rows, 1):
            description += f"{i}. {emoji} - {count} 次\n"
            total_count += count

        embed.description = description
        embed.set_footer(text=f"伺服器總共使用 {total_count} 個表情符號")

        logger.info(
            "User %s(%d) requested guild emoji statistics for guild %s(%d)",
            interaction.user,
            uid,
            interaction.guild.name,
            interaction.guild.id,
        )
    else:
        # Get top 10 most used emojis for this user
        res = conn.execute(
            """
            SELECT emoji, count 
            FROM emoji_usage 
            WHERE user_id = ? 
            ORDER BY count DESC 
            LIMIT 10
            """, 
            (uid,)
        )
        rows = res.fetchall()
        
        if not rows:
            try:
                await interaction.followup.send(
                    "📊 你還沒有使用過任何表情符號！", 
                    ephemeral=True
                )
            except Exception as e:
                logger.exception(
                    "Error sending emoji stats to user %s(%d): %s",
                    interaction.user,
                    uid,
                    e,
                )
            return

        # Create embed with emoji statistics
        embed = discord.Embed(
            title="📊 你的表情符號使用排行榜",
            color=0x3498DB,
            timestamp=interaction.created_at
        )

        description = ""
        total_count = 0
        
        for i, (emoji, count) in enumerate(rows, 1):
            description += f"{i}. {emoji} - {count} 次\n"
            total_count += count

        embed.description = description
        embed.set_footer(text=f"總共使用 {total_count} 個表情符號")

        logger.info(
            "User %s(%d) requested their emoji statistics",
            interaction.user,
            uid,
        )

    conn.close()

    try:
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.exception(
            "Error sending emoji stats to user %s(%d): %s",
            interaction.user,
            uid,
            e,
        )


@bot.tree.command(name="clear_emoji_stats", description="[管理員] 清除表情符號統計資料")
async def clear_emoji_stats(interaction: discord.Interaction):
    # Check if user is admin
    if interaction.user.id not in ADMIN_USER_IDS:
        try:
            await interaction.response.send_message(
                "❌ 此命令僅限管理員使用！", 
                ephemeral=True
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
                "✅ 已清除所有表情符號統計資料！", 
                ephemeral=True
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
                f"⚠️ 清除資料時發生錯誤：{str(e)}", 
                ephemeral=True
            )
        except Exception as e2:
            logger.exception(
                "Error sending clear error message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e2,
            )
@app_commands.describe(
    channel="要掃描的頻道（若不指定且 scan_guild=False，則為當前頻道）",
    limit="每個頻道的掃描訊息數量上限（預設 1000，當 unlimited=True 時無效）",
    scan_guild="是否掃描整個伺服器（預設 False）",
    unlimited="是否不限制訊息數量（僅對 scan_guild=True 有效，預設 False）"
)
async def scan_emoji_history(
    interaction: discord.Interaction, 
    channel: discord.TextChannel = None, 
    limit: int = 1000,
    scan_guild: bool = False,
    unlimited: bool = False
):
    # Check if user is admin
    if interaction.user.id not in ADMIN_USER_IDS:
        try:
            await interaction.response.send_message(
                "❌ 此命令僅限管理員使用！", 
                ephemeral=True
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
                "❌ 訊息數量限制必須在 1-10000 之間！", 
                ephemeral=True
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
                        "❌ 此命令只能在伺服器中使用！", 
                        ephemeral=True
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
                    ephemeral=True
                )
            else:
                progress_msg = await interaction.followup.send(
                    f"🔍 開始掃描伺服器 `{interaction.guild.name}` 的所有文字頻道...\n"
                    f"每個頻道訊息上限：{limit}\n"
                    f"請稍候...",
                    ephemeral=True
                )

            # Perform the guild scan
            messages_scanned, emojis_found, channels_scanned = await bot.scan_guild_history(interaction.guild, limit, unlimited)

            # Update progress message with results
            embed = discord.Embed(
                title="✅ 伺服器歷史訊息掃描完成",
                color=0x2ECC71,
                timestamp=interaction.created_at
            )
            
            embed.add_field(
                name="📊 掃描結果",
                value=f"伺服器：{interaction.guild.name}\n"
                      f"掃描頻道：{channels_scanned}\n"
                      f"掃描訊息：{messages_scanned}\n"
                      f"發現表情符號：{emojis_found}",
                inline=False
            )
            
            if messages_scanned > 0:
                avg_emojis = emojis_found / messages_scanned
                embed.add_field(
                    name="📈 統計資訊",
                    value=f"平均每訊息表情符號：{avg_emojis:.2f}",
                    inline=True
                )
            
            if channels_scanned > 0:
                avg_channels = messages_scanned / channels_scanned
                embed.add_field(
                    name="📈 頻道統計",
                    value=f"平均每頻道訊息：{avg_channels:.1f}",
                    inline=True
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
                        "❌ 只能掃描文字頻道！", 
                        ephemeral=True
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
                ephemeral=True
            )

            # Perform the scan
            messages_scanned, emojis_found = await bot.scan_channel_history(target_channel, limit)

            # Update progress message with results
            embed = discord.Embed(
                title="✅ 頻道歷史訊息掃描完成",
                color=0x2ECC71,
                timestamp=interaction.created_at
            )
            
            embed.add_field(
                name="📊 掃描結果",
                value=f"頻道：#{target_channel.name}\n"
                      f"掃描訊息：{messages_scanned}\n"
                      f"發現表情符號：{emojis_found}",
                inline=False
            )
            
            if messages_scanned > 0:
                avg_emojis = emojis_found / messages_scanned
                embed.add_field(
                    name="📈 統計資訊",
                    value=f"平均每訊息表情符號：{avg_emojis:.2f}",
                    inline=True
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
                f"⚠️ 掃描過程中發生錯誤：{str(e)}", 
                ephemeral=True
            )
        except Exception as e2:
            logger.exception(
                "Error sending scan error message to user %s(%d): %s",
                interaction.user,
                interaction.user.id,
                e2,
            )


