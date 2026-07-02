"""YouTube channel-membership verification via Google OAuth.

Mechanism (member-side, no channel-owner authorization needed):
  1. The Discord user authorizes with their Google account (youtube.readonly).
  2. We store their (encrypted) refresh token.
  3. To verify, we use *their* token to call commentThreads.list on a
     members-only video of the target channel. 200 => they can read
     members-only comments => they are a member; 403 => not a member.

Members-only videos are auto-discovered from the channel's members-only
uploads playlist: replace the "UC" channel-id prefix with "UUMO".
"""

import asyncio
import base64
import hashlib
import hmac
import os
import sqlite3
import time
import urllib.parse

import aiohttp
import discord
from aiohttp import web

from config import (
    DB_PATH,
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_REDIRECT_URI,
    MEMBERSHIP_CHECK_INTERVAL,
    MEMBERSHIP_GUILD_ID,
    MEMBERSHIP_OAUTH_HOST,
    MEMBERSHIP_OAUTH_PORT,
    MEMBERSHIP_OAUTH_SCOPE,
    MEMBERSHIP_SUCCESS_REDIRECT,
    MEMBERSHIP_TOKEN_ENC_KEY,
    YOUTUBE_API_KEY,
    logger,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YT_API_BASE = "https://www.googleapis.com/youtube/v3"

# Probe at most this many members-only videos before giving up (quota control).
MAX_PROBE_VIDEOS = 3
# Cache auto-discovered probe video ids for this long.
PROBE_CACHE_TTL = 3600

MEMBERSHIP_SCHEMA = """
    CREATE TABLE IF NOT EXISTS membership_oauth (
        discord_user_id INTEGER PRIMARY KEY,
        youtube_channel_id TEXT,
        refresh_token_enc TEXT NOT NULL,
        last_checked INTEGER,
        created_at INTEGER
    )
"""

# Admin-managed channel -> role mappings, per guild (one role per channel per
# guild). The same YouTube channel can map to different roles in different
# servers, so the bot can serve multiple guilds from one instance.
MEMBERSHIP_CHANNELS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS membership_channels (
        guild_id INTEGER NOT NULL,
        yt_channel_id TEXT NOT NULL,
        role_id INTEGER NOT NULL,
        added_by INTEGER,
        created_at INTEGER,
        PRIMARY KEY (guild_id, yt_channel_id)
    )
"""


def members_only_playlist_id(channel_id: str) -> str | None:
    """UC<...> channel id -> UUMO<...> members-only uploads playlist id."""
    if channel_id and channel_id.startswith("UC") and len(channel_id) > 2:
        return "UUMO" + channel_id[2:]
    return None


def decide_membership(probe_results: list[tuple[int, str]]) -> bool | None:
    """Decide membership from (http_status, error_reason) probe results.

    True  -> a probe on a members-only video returned 200 (member).
    False -> a probe was forbidden (403, not "commentsDisabled") => non-member.
    None  -> everything was inconclusive (comments disabled / gone / quota / net).
    """
    saw_forbidden = False
    for status, reason in probe_results:
        if status == 200:
            return True
        if status == 403 and reason != "commentsDisabled":
            saw_forbidden = True
    return False if saw_forbidden else None


class MembershipMixin:
    db_path = DB_PATH

    # ---- enablement / schema -------------------------------------------------

    @property
    def membership_enabled(self) -> bool:
        # The OAuth infrastructure is available once credentials + key are set;
        # channel->role mappings (per guild) are added by admins at runtime.
        return bool(
            GOOGLE_OAUTH_CLIENT_ID
            and GOOGLE_OAUTH_CLIENT_SECRET
            and GOOGLE_OAUTH_REDIRECT_URI
            and self._fernet is not None
        )

    def ensure_membership_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(MEMBERSHIP_SCHEMA)
        # Migrate an older single-guild membership_channels table (no guild_id)
        # by backfilling the legacy MEMBERSHIP_GUILD_ID.
        cols = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(membership_channels)"
            ).fetchall()
        ]
        if cols and "guild_id" not in cols:
            backfill = MEMBERSHIP_GUILD_ID or 0
            logger.info(
                "Migrating membership_channels to per-guild schema "
                "(backfilling guild_id=%s)...",
                backfill,
            )
            conn.execute(
                "ALTER TABLE membership_channels RENAME TO membership_channels_legacy"
            )
            conn.execute(MEMBERSHIP_CHANNELS_SCHEMA)
            conn.execute(
                """
                INSERT INTO membership_channels
                    (guild_id, yt_channel_id, role_id, added_by, created_at)
                SELECT ?, yt_channel_id, role_id, added_by, created_at
                FROM membership_channels_legacy
                """,
                (backfill,),
            )
            conn.execute("DROP TABLE membership_channels_legacy")
            logger.info("membership_channels migration complete.")
        else:
            conn.execute(MEMBERSHIP_CHANNELS_SCHEMA)

    # ---- channel -> role mappings (admin-managed) ----------------------------

    def load_membership_channels(self) -> None:
        """Load per-guild channel->role mappings from the DB into memory."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT guild_id, yt_channel_id, role_id FROM membership_channels"
        ).fetchall()
        conn.close()
        # [ (guild_id, yt_channel_id, role_id), ... ]
        self.membership_channel_map = [
            (guild_id, ch, role_id) for guild_id, ch, role_id in rows
        ]
        logger.info(
            "Loaded %d membership channel mapping(s)",
            len(self.membership_channel_map),
        )

    def add_membership_channel(
        self, guild_id: int, yt_channel_id: str, role_id: int, added_by: int
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO membership_channels
                (guild_id, yt_channel_id, role_id, added_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, yt_channel_id) DO UPDATE SET
                role_id = excluded.role_id,
                added_by = excluded.added_by
            """,
            (guild_id, yt_channel_id, role_id, added_by, int(time.time())),
        )
        conn.commit()
        conn.close()
        self.load_membership_channels()

    def remove_membership_channel(self, guild_id: int, yt_channel_id: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "DELETE FROM membership_channels WHERE guild_id=? AND yt_channel_id=?",
            (guild_id, yt_channel_id),
        )
        removed = cur.rowcount > 0
        conn.commit()
        conn.close()
        if removed:
            self.load_membership_channels()
        return removed

    # ---- refresh-token encryption at rest ------------------------------------

    @property
    def _fernet(self):
        cached = getattr(self, "_fernet_cache", False)
        if cached is not False:
            return cached
        cipher = None
        if MEMBERSHIP_TOKEN_ENC_KEY:
            try:
                from cryptography.fernet import Fernet

                cipher = Fernet(MEMBERSHIP_TOKEN_ENC_KEY.encode())
            except Exception:
                logger.exception(
                    "Invalid MEMBERSHIP_TOKEN_ENC_KEY; membership verification "
                    "disabled. Generate one with: python -c \"from "
                    "cryptography.fernet import Fernet; "
                    'print(Fernet.generate_key().decode())"'
                )
        self._fernet_cache = cipher
        return cipher

    def _encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def _decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    # ---- signed OAuth state (unforgeable, short-lived) -----------------------

    def _state_secret(self) -> bytes:
        return hashlib.sha256(
            ("ytmember-state:" + GOOGLE_OAUTH_CLIENT_SECRET).encode()
        ).digest()

    def sign_state(self, discord_user_id: int, ttl: int = 900) -> str:
        payload = f"{discord_user_id}.{int(time.time()) + ttl}"
        sig = hmac.new(
            self._state_secret(), payload.encode(), hashlib.sha256
        ).hexdigest()[:32]
        return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()

    def verify_state(self, state: str) -> int | None:
        try:
            raw = base64.urlsafe_b64decode(state.encode()).decode()
            uid_str, exp_str, sig = raw.rsplit(".", 2)
            expected = hmac.new(
                self._state_secret(), f"{uid_str}.{exp_str}".encode(), hashlib.sha256
            ).hexdigest()[:32]
            if not hmac.compare_digest(expected, sig):
                return None
            if int(exp_str) < int(time.time()):
                return None
            return int(uid_str)
        except Exception:
            return None

    def build_oauth_url(self, discord_user_id: int) -> str:
        params = {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": MEMBERSHIP_OAUTH_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": self.sign_state(discord_user_id),
        }
        return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)

    # ---- Google token endpoints ----------------------------------------------

    async def exchange_code(
        self, session: aiohttp.ClientSession, code: str
    ) -> dict | None:
        data = {
            "code": code,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        try:
            async with session.post(GOOGLE_TOKEN_URL, data=data, timeout=20) as resp:
                body = await resp.json()
                if resp.status != 200:
                    logger.warning(
                        "OAuth code exchange failed %d: %s",
                        resp.status,
                        body.get("error"),
                    )
                    return None
                return body
        except Exception:
            logger.exception("OAuth code exchange request error")
            return None

    async def refresh_access_token(
        self, session: aiohttp.ClientSession, refresh_token: str
    ) -> tuple[str | None, bool]:
        """Return (access_token, revoked). revoked=True on invalid_grant."""
        data = {
            "refresh_token": refresh_token,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }
        try:
            async with session.post(GOOGLE_TOKEN_URL, data=data, timeout=20) as resp:
                body = await resp.json()
                if resp.status == 200:
                    return body.get("access_token"), False
                error = (body or {}).get("error")
                logger.warning("Token refresh failed %d: %s", resp.status, error)
                return None, error == "invalid_grant"
        except Exception:
            logger.exception("Token refresh request error")
            return None, False

    async def revoke_token(
        self, session: aiohttp.ClientSession, refresh_token: str
    ) -> None:
        try:
            await session.post(
                GOOGLE_REVOKE_URL, data={"token": refresh_token}, timeout=15
            )
        except Exception:
            logger.exception("Token revoke request error")

    # ---- YouTube Data API ----------------------------------------------------

    async def get_user_channel_id(
        self, session: aiohttp.ClientSession, access_token: str
    ) -> str | None:
        try:
            async with session.get(
                f"{YT_API_BASE}/channels",
                params={"part": "id", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            ) as resp:
                body = await resp.json()
                items = body.get("items") if resp.status == 200 else None
                if items:
                    return items[0].get("id")
        except Exception:
            logger.exception("channels.list mine=true failed")
        return None

    async def _list_playlist_video_ids(
        self,
        session: aiohttp.ClientSession,
        playlist_id: str,
        access_token: str | None,
    ) -> list[str]:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": "5",
        }
        headers = {}
        if YOUTUBE_API_KEY:
            params["key"] = YOUTUBE_API_KEY
        elif access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        else:
            return []
        try:
            async with session.get(
                f"{YT_API_BASE}/playlistItems",
                params=params,
                headers=headers,
                timeout=20,
            ) as resp:
                body = await resp.json()
                if resp.status != 200:
                    logger.warning(
                        "playlistItems.list %d: %s",
                        resp.status,
                        (body or {}).get("error", {}).get("message"),
                    )
                    return []
                return [
                    it["contentDetails"]["videoId"]
                    for it in body.get("items", [])
                    if it.get("contentDetails", {}).get("videoId")
                ]
        except Exception:
            logger.exception("playlistItems.list request error")
            return []

    async def get_probe_video_ids(
        self,
        session: aiohttp.ClientSession,
        yt_channel_id: str,
        access_token: str | None = None,
    ) -> list[str]:
        now = int(time.time())
        cached = self._probe_cache.get(yt_channel_id)
        if cached and now - cached["ts"] < PROBE_CACHE_TTL:
            return cached["ids"]
        playlist_id = members_only_playlist_id(yt_channel_id)
        if not playlist_id:
            return cached["ids"] if cached else []
        vids = await self._list_playlist_video_ids(session, playlist_id, access_token)
        if vids:
            self._probe_cache[yt_channel_id] = {"ids": vids, "ts": now}
            return vids
        return cached["ids"] if cached else []

    async def _probe_comment_thread(
        self, session: aiohttp.ClientSession, video_id: str, access_token: str
    ) -> tuple[int, str]:
        try:
            async with session.get(
                f"{YT_API_BASE}/commentThreads",
                params={"part": "id", "videoId": video_id, "maxResults": "1"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            ) as resp:
                if resp.status == 200:
                    return 200, ""
                body = await resp.json()
                errors = (body or {}).get("error", {}).get("errors", [])
                reason = errors[0].get("reason", "") if errors else ""
                return resp.status, reason
        except Exception:
            logger.exception("commentThreads.list probe error for %s", video_id)
            return 0, "exception"

    async def check_is_member(
        self, session: aiohttp.ClientSession, access_token: str, yt_channel_id: str
    ) -> bool | None:
        probe_videos = await self.get_probe_video_ids(
            session, yt_channel_id, access_token
        )
        if not probe_videos:
            logger.warning(
                "No members-only probe videos for %s (set YOUTUBE_API_KEY for reliable auto-discovery); cannot verify",
                yt_channel_id,
            )
            return None
        results: list[tuple[int, str]] = []
        for video_id in probe_videos[:MAX_PROBE_VIDEOS]:
            status, reason = await self._probe_comment_thread(
                session, video_id, access_token
            )
            results.append((status, reason))
            if status == 200:
                break
        return decide_membership(results)

    # ---- Discord role sync ---------------------------------------------------

    async def apply_member_role(
        self, discord_user_id: int, guild_id: int, role_id: int, is_member: bool
    ) -> None:
        guild = self.get_guild(guild_id)
        role = guild.get_role(role_id) if guild else None
        if guild is None or role is None:
            logger.warning(
                "Membership guild/role not found (guild=%s role=%s)",
                guild_id,
                role_id,
            )
            return
        member = guild.get_member(discord_user_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_user_id)
            except Exception:
                return
        try:
            if is_member and role not in member.roles:
                await member.add_roles(role, reason="YouTube membership verified")
            elif not is_member and role in member.roles:
                await member.remove_roles(role, reason="YouTube membership inactive")
        except discord.Forbidden:
            logger.warning(
                "Missing permission to manage role %s in guild %s", role.id, guild.id
            )
        except Exception:
            logger.exception("Failed to sync membership role for %d", discord_user_id)

    # ---- storage -------------------------------------------------------------

    def store_membership(
        self, discord_user_id: int, yt_channel_id: str | None, refresh_token: str
    ) -> None:
        now = int(time.time())
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO membership_oauth
                (discord_user_id, youtube_channel_id, refresh_token_enc, last_checked, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                youtube_channel_id = excluded.youtube_channel_id,
                refresh_token_enc = excluded.refresh_token_enc,
                last_checked = excluded.last_checked
            """,
            (discord_user_id, yt_channel_id, self._encrypt(refresh_token), now, now),
        )
        conn.commit()
        conn.close()

    def _touch_last_checked(self, discord_user_id: int) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE membership_oauth SET last_checked=? WHERE discord_user_id=?",
            (int(time.time()), discord_user_id),
        )
        conn.commit()
        conn.close()

    def get_membership_row(self, discord_user_id: int):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT discord_user_id, youtube_channel_id, refresh_token_enc, last_checked "
            "FROM membership_oauth WHERE discord_user_id=?",
            (discord_user_id,),
        ).fetchone()
        conn.close()
        return row

    def _all_membership_rows(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT discord_user_id, youtube_channel_id, refresh_token_enc, last_checked "
            "FROM membership_oauth"
        ).fetchall()
        conn.close()
        return rows

    def _delete_membership(self, discord_user_id: int) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM membership_oauth WHERE discord_user_id=?", (discord_user_id,)
        )
        conn.commit()
        conn.close()

    # ---- verification orchestration ------------------------------------------

    async def check_all_channels(
        self, session: aiohttp.ClientSession, discord_user_id: int, access_token: str
    ) -> dict:
        """Check every configured channel with one token and sync each role.

        Returns {(guild_id, yt_channel_id): True | False | None}. None
        (inconclusive) leaves that mapping's role untouched.
        """
        results: dict[tuple[int, str], bool | None] = {}
        # A channel's membership result is guild-independent, so probe each
        # distinct channel only once even if it's mapped in several guilds.
        channel_cache: dict[str, bool | None] = {}
        for guild_id, yt_channel_id, role_id in self.membership_channel_map:
            if yt_channel_id in channel_cache:
                is_member = channel_cache[yt_channel_id]
            else:
                is_member = await self.check_is_member(
                    session, access_token, yt_channel_id
                )
                channel_cache[yt_channel_id] = is_member
            results[(guild_id, yt_channel_id)] = is_member
            if is_member is not None:
                await self.apply_member_role(
                    discord_user_id, guild_id, role_id, is_member
                )
        self._touch_last_checked(discord_user_id)
        return results

    async def verify_membership(
        self, session: aiohttp.ClientSession, discord_user_id: int, refresh_token: str
    ) -> dict | None:
        access_token, revoked = await self.refresh_access_token(session, refresh_token)
        if access_token is None:
            if revoked:
                logger.info(
                    "Membership authorization revoked for %d; removing roles/record",
                    discord_user_id,
                )
                for guild_id, _ch, role_id in self.membership_channel_map:
                    await self.apply_member_role(
                        discord_user_id, guild_id, role_id, False
                    )
                self._delete_membership(discord_user_id)
            return None
        return await self.check_all_channels(session, discord_user_id, access_token)

    async def unlink_membership(self, discord_user_id: int) -> bool:
        row = self.get_membership_row(discord_user_id)
        if not row:
            return False
        try:
            refresh_token = self._decrypt(row[2])
            async with aiohttp.ClientSession() as session:
                await self.revoke_token(session, refresh_token)
        except Exception:
            logger.exception("Error revoking token during unlink for %d", discord_user_id)
        for guild_id, _ch, role_id in self.membership_channel_map:
            await self.apply_member_role(discord_user_id, guild_id, role_id, False)
        self._delete_membership(discord_user_id)
        return True

    async def membership_recheck_all(self) -> None:
        rows = self._all_membership_rows()
        if not rows:
            return
        logger.info("Re-checking membership for %d linked user(s)", len(rows))
        async with aiohttp.ClientSession() as session:
            for discord_user_id, _yt, refresh_token_enc, _last in rows:
                try:
                    refresh_token = self._decrypt(refresh_token_enc)
                except Exception:
                    logger.exception(
                        "Failed to decrypt token for %d; skipping", discord_user_id
                    )
                    continue
                await self.verify_membership(session, discord_user_id, refresh_token)
                await asyncio.sleep(1)  # gentle on quota / rate limits

    async def membership_monitor(self) -> None:
        await asyncio.sleep(15)  # let the guild/member cache warm up
        while True:
            try:
                await self.membership_recheck_all()
            except Exception:
                logger.exception("Membership monitor error")
            await asyncio.sleep(MEMBERSHIP_CHECK_INTERVAL)

    # ---- OAuth callback web server -------------------------------------------

    async def start_membership_server(self) -> None:
        if getattr(self, "_membership_runner", None) is not None:
            return  # already running (on_ready may fire again on reconnect)
        app = web.Application()
        app.router.add_get("/oauth/callback", self._handle_oauth_callback)
        app.router.add_get("/healthz", self._handle_healthz)
        # Privacy policy / terms served on the same host (handy for the Google
        # consent screen, and works even in a tunnel-only setup with no nginx).
        app.router.add_get("/privacy.html", self._handle_privacy)
        app.router.add_get("/terms.html", self._handle_terms)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, MEMBERSHIP_OAUTH_HOST, MEMBERSHIP_OAUTH_PORT)
        await site.start()
        self._membership_runner = runner
        logger.info(
            "Membership OAuth server listening on %s:%d",
            MEMBERSHIP_OAUTH_HOST,
            MEMBERSHIP_OAUTH_PORT,
        )

    async def _handle_healthz(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _handle_privacy(self, request: web.Request) -> web.Response:
        return self._serve_static_file("privacy.html")

    async def _handle_terms(self, request: web.Request) -> web.Response:
        return self._serve_static_file("terms.html")

    def _serve_static_file(self, name: str) -> web.Response:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static", name
        )
        try:
            with open(path, encoding="utf-8") as fh:
                return web.Response(text=fh.read(), content_type="text/html")
        except OSError:
            return web.Response(status=404, text="Not found")

    async def _handle_oauth_callback(self, request: web.Request):
        error = request.query.get("error")
        if error:
            return self._oauth_page(f"授權失敗：{error}", ok=False)
        state = request.query.get("state", "")
        code = request.query.get("code")
        discord_user_id = self.verify_state(state)
        if discord_user_id is None:
            return self._oauth_page(
                "連結已失效或無效，請重新在 Discord 執行 /verify_membership。", ok=False
            )
        if not code:
            return self._oauth_page("缺少授權碼，請重試。", ok=False)

        async with aiohttp.ClientSession() as session:
            tokens = await self.exchange_code(session, code)
            if not tokens or not tokens.get("refresh_token"):
                return self._oauth_page(
                    "無法取得離線授權，請重試並在同意畫面允許所有權限。", ok=False
                )
            refresh_token = tokens["refresh_token"]
            access_token = tokens.get("access_token")
            yt_channel_id = (
                await self.get_user_channel_id(session, access_token)
                if access_token
                else None
            )
            self.store_membership(discord_user_id, yt_channel_id, refresh_token)
            results = (
                await self.check_all_channels(session, discord_user_id, access_token)
                if access_token
                else {}
            )

        granted = sum(1 for ok in results.values() if ok)
        total = len(self.membership_channel_map)
        if granted:
            logger.info(
                "Membership verified for %d: %d/%d channel(s)",
                discord_user_id,
                granted,
                total,
            )
            return self._oauth_page(
                f"✅ 驗證完成！已授予你符合資格的 {granted}/{total} 個頻道會員身分組。"
                "你可以關閉此頁面。",
                ok=True,
            )
        if results and all(v is False for v in results.values()):
            logger.info("Membership NOT active for %d on any channel", discord_user_id)
            return self._oauth_page(
                "已連結你的 YouTube 帳號，但未偵測到任何設定頻道的會員資格。", ok=False
            )
        logger.info("Membership inconclusive for %d (will retry)", discord_user_id)
        return self._oauth_page(
            "已連結你的 YouTube 帳號，但目前無法確認會員資格，稍後會自動重試。", ok=None
        )

    def _oauth_page(self, message: str, ok: bool | None):
        if MEMBERSHIP_SUCCESS_REDIRECT:
            return web.HTTPFound(MEMBERSHIP_SUCCESS_REDIRECT)

        from html import escape

        safe_message = escape(message)
        color = "#2ecc71" if ok else ("#e74c3c" if ok is False else "#f1c40f")
        html = (
            '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>YouTube 會員驗證</title></head>"
            '<body style="font-family:system-ui,sans-serif;background:#1e1f22;'
            'color:#eee;display:flex;align-items:center;justify-content:center;'
            'min-height:100vh;margin:0;padding:1rem">'
            f'<div style="max-width:460px;padding:2rem;border-radius:12px;'
            f'background:#2b2d31;border-top:4px solid {color};text-align:center">'
            "<h2>YouTube 會員驗證</h2>"
            f"<p style=\"line-height:1.6\">{safe_message}</p></div></body></html>"
        )
        return web.Response(text=html, content_type="text/html")
