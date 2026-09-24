"""YouTube video summaries (``/summarize``).

Pipeline (see plan.md in the youtube-transcript repo):

1. Cache lookup by video id (SQLite ``summary_cache``); a hit answers instantly.
2. Transcribe on the home transcribe service (youtube-transcript server.py)
   over Tailscale: ``POST /jobs`` then poll ``GET /jobs/{id}`` until done. It
   returns manual subtitles when the video has them (``source=subs``),
   otherwise a whisper transcript.
3. If the service is unset / unreachable / full (429) / errors / exceeds
   ``TRANSCRIBE_JOB_TIMEOUT``, fall back on the VPS: yt-dlp (manual subs first, then
   audio) + ffmpeg to 16 kHz mono opus chunks, transcribed by Groq.
4. Summarize the transcript in Japanese with ``SUMMARY_MODEL``.

The transcript is cached as soon as it exists, so a failed summary retries
without re-transcribing. Concurrent requests for the same video share one run.
"""

import asyncio
import glob
import json
import os
import re
import sqlite3
import tempfile
import time

import aiohttp

from config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_CHUNK_SECONDS,
    GROQ_TRANSCRIBE_MODEL,
    TRANSCRIBE_CONNECT_TIMEOUT,
    TRANSCRIBE_JOB_TIMEOUT,
    TRANSCRIBE_POLL_INTERVAL,
    TRANSCRIBE_TOKEN,
    TRANSCRIBE_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    RECORDING_COOKIE_FILE,
    SUMMARY_MAX_TRANSCRIPT_CHARS,
    SUMMARY_MODEL,
    SUMMARY_REASONING_EFFORT,
    logger,
)

SUMMARY_LANG = "ja"

# Consecutive failed polls tolerated before giving up on a transcribe job.
_TRANSCRIBE_MAX_POLL_ERRORS = 3
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_VIDEO_ID_RE = re.compile(
    r"(?:v=|/live/|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})"
)
_VTT_TS = re.compile(r"^\d{2}:\d{2}(:\d{2})?\.\d{3} --> ")
_TAG = re.compile(r"<[^>]+>")

SUMMARY_CACHE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS summary_cache (
        video_id TEXT PRIMARY KEY,
        title TEXT,
        transcript TEXT NOT NULL,
        source TEXT NOT NULL,
        transcribe_model TEXT,
        summary TEXT,
        summary_model TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
"""

SUMMARY_SYSTEM_PROMPT = (
    "あなたはYouTube動画(主にVTuberの配信)の書き起こしを要約するアシスタントです。"
    "書き起こしは音声認識の結果なので、誤認識や言い間違いが含まれます。文脈から自然に補ってください。\n"
    "出力は日本語のMarkdownで、次の形式に従ってください:\n"
    "## 要点\n"
    "- 話題や出来事を時系列で箇条書き(5〜15項目、各1〜2文)\n"
    "## まとめ\n"
    "動画全体を一文で。\n"
    "書き起こしにない内容を推測で付け加えないでください。"
)


class SummarizeError(Exception):
    """A failure whose message is safe to show to the Discord user."""


def parse_video_id(target: str) -> str | None:
    """Return the 11-char video id from a bare id or a common YouTube URL."""
    target = (target or "").strip()
    if _VIDEO_ID_RE.match(target):
        return target
    match = _URL_VIDEO_ID_RE.search(target)
    return match.group(1) if match else None


def vtt_to_text(vtt: str) -> str:
    """Strip WebVTT cue timings/tags, dropping consecutive duplicate lines."""
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or _VTT_TS.match(line) or line.isdigit():
            continue
        if line.startswith(("Kind:", "Language:", "NOTE", "STYLE")):
            continue
        line = _TAG.sub("", line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


class SummarizeMixin:
    # self._summarize_inflight: { video_id: asyncio.Task } and
    # self._summarize_fallback_sem are initialized in MyBot.__init__.
    # Uses RecordingMixin._yt_dlp_command / _run_process for the VPS fallback.

    def summarize_available(self) -> str | None:
        """Return None when /summarize can run, else a user-facing reason."""
        if not OPENAI_API_KEY:
            return "摘要功能未啟用：尚未設定 OPENAI_API_KEY。"
        if not TRANSCRIBE_URL and not GROQ_API_KEY:
            return "摘要功能未啟用：尚未設定 TRANSCRIBE_URL 或 GROQ_API_KEY。"
        return None

    # ---- cache ---------------------------------------------------------------

    def ensure_summary_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(SUMMARY_CACHE_SCHEMA)

    def _load_summary_cache(self, video_id: str) -> dict | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM summary_cache WHERE video_id = ?", (video_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def _save_transcript(self, video_id: str, result: dict) -> None:
        now = int(time.time())
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO summary_cache
                (video_id, title, transcript, source, transcribe_model,
                 summary, summary_model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                transcript = excluded.transcript,
                source = excluded.source,
                transcribe_model = excluded.transcribe_model,
                summary = NULL,
                summary_model = NULL,
                updated_at = excluded.updated_at
            """,
            (
                video_id,
                result.get("title"),
                result["transcript"],
                result["source"],
                result.get("transcribe_model"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

    def _save_summary(self, video_id: str, summary: str, model: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE summary_cache SET summary = ?, summary_model = ?, updated_at = ? "
            "WHERE video_id = ?",
            (summary, model, int(time.time()), video_id),
        )
        conn.commit()
        conn.close()

    # ---- entry point ---------------------------------------------------------

    async def summarize_video(self, video_id: str, progress=None) -> dict:
        """Return the cached/new summary row for ``video_id``.

        ``progress`` is an optional ``async (text) -> None`` status callback.
        Requests for a video already in flight join that run (without progress
        updates). Raises SummarizeError with a user-facing message on failure.
        """
        task = self._summarize_inflight.get(video_id)
        if task is None:
            task = asyncio.create_task(self._summarize_pipeline(video_id, progress))
            self._summarize_inflight[video_id] = task
            task.add_done_callback(
                lambda _t: self._summarize_inflight.pop(video_id, None)
            )
        elif progress:
            await progress("⏳ 這支影片已在處理中，完成後一併回覆…")
        return await asyncio.shield(task)

    async def _summarize_pipeline(self, video_id: str, progress) -> dict:
        async def report(text: str) -> None:
            if progress:
                try:
                    await progress(text)
                except Exception:
                    logger.debug("Summarize progress update failed", exc_info=True)

        row = await asyncio.to_thread(self._load_summary_cache, video_id)
        if row and row.get("summary"):
            return {**row, "cached": True}

        if not row:
            url = f"https://www.youtube.com/watch?v={video_id}"
            result = await self._transcribe_via_service(url, report)
            if result is None:
                result = await self._transcribe_via_groq(url, report)
            if not result.get("transcript", "").strip():
                raise SummarizeError("取得的逐字稿是空的，影片可能沒有語音內容。")
            await asyncio.to_thread(self._save_transcript, video_id, result)
            row = await asyncio.to_thread(self._load_summary_cache, video_id)

        await report("✍️ 產生摘要中…")
        summary = await self._summarize_transcript(row.get("title"), row["transcript"])
        await asyncio.to_thread(self._save_summary, video_id, summary, SUMMARY_MODEL)
        row.update(summary=summary, summary_model=SUMMARY_MODEL)
        return {**row, "cached": False}

    # ---- transcribe service -----------------------------------------------

    async def _transcribe_via_service(self, url: str, report) -> dict | None:
        """Transcribe on the transcribe service. Returns None (after logging) when we should fall back."""
        if not TRANSCRIBE_URL:
            return None
        headers = {"Authorization": f"Bearer {TRANSCRIBE_TOKEN}"}
        timeout = aiohttp.ClientTimeout(total=30, connect=TRANSCRIBE_CONNECT_TIMEOUT)
        deadline = time.monotonic() + TRANSCRIBE_JOB_TIMEOUT
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.post(
                    f"{TRANSCRIBE_URL}/jobs", json={"url": url, "lang": SUMMARY_LANG}
                ) as resp:
                    if resp.status == 429:
                        logger.info("Transcribe queue full; falling back to Groq for %s", url)
                        await report("⚠️ 地端轉錄排隊已滿，改用雲端轉錄…")
                        return None
                    if resp.status != 202:
                        body = (await resp.text())[:300]
                        logger.warning("Transcribe POST /jobs -> %s: %s", resp.status, body)
                        return None
                    job = await resp.json()

                job_id = job["job_id"]
                errors = 0
                last_status = None
                while True:
                    status = job.get("status")
                    if status == "done":
                        res = job.get("result") or {}
                        return {
                            "title": res.get("title"),
                            "transcript": res.get("transcript") or "",
                            "source": "subs" if res.get("source") == "subs" else "whisper",
                            "transcribe_model": res.get("model"),
                        }
                    if status == "error":
                        logger.warning("Transcribe job %s failed: %s", job_id, job.get("error"))
                        return None
                    if status == "queued":
                        text = f"⏳ 地端轉錄排隊中（第 {job.get('position', '?')} 位）…"
                    else:
                        text = "🎧 地端轉錄中…（長片可能需要數分鐘）"
                    if text != last_status:
                        await report(text)
                        last_status = text

                    if time.monotonic() >= deadline:
                        logger.warning(
                            "Transcribe job %s still %s after %ds; falling back to Groq",
                            job_id,
                            status,
                            TRANSCRIBE_JOB_TIMEOUT,
                        )
                        return None
                    await asyncio.sleep(TRANSCRIBE_POLL_INTERVAL)

                    try:
                        async with session.get(f"{TRANSCRIBE_URL}/jobs/{job_id}") as resp:
                            if resp.status == 404:
                                # Expired, or the service restarted and lost its in-memory jobs.
                                logger.warning("Transcribe job %s disappeared", job_id)
                                return None
                            resp.raise_for_status()
                            job = await resp.json()
                        errors = 0
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        errors += 1
                        logger.warning(
                            "Polling transcribe job %s failed (%d/%d): %s",
                            job_id,
                            errors,
                            _TRANSCRIBE_MAX_POLL_ERRORS,
                            exc,
                        )
                        if errors >= _TRANSCRIBE_MAX_POLL_ERRORS:
                            return None
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ValueError) as exc:
            logger.warning(
                "Transcribe service %s unavailable (%s: %s); falling back to Groq",
                TRANSCRIBE_URL,
                type(exc).__name__,
                exc,
            )
            return None

    # ---- VPS fallback: yt-dlp + ffmpeg + Groq ----------------------------------

    def _yt_dlp_base(self) -> list[str]:
        cmd = [
            *self._yt_dlp_command(),
            "--ignore-config",
            "--no-color",
            "--no-playlist",
            "--remote-components",
            "ejs:npm",
        ]
        if RECORDING_COOKIE_FILE and os.path.isfile(RECORDING_COOKIE_FILE):
            cmd += ["--cookies", RECORDING_COOKIE_FILE]
        return cmd

    async def _transcribe_via_groq(self, url: str, report) -> dict:
        if not GROQ_API_KEY:
            raise SummarizeError("地端轉錄服務目前無法使用，且未設定雲端備援（GROQ_API_KEY）。")
        if not self.recording_available():
            raise SummarizeError("地端轉錄服務目前無法使用，且找不到 yt-dlp 可供備援。")

        async with self._summarize_fallback_sem:
            with tempfile.TemporaryDirectory(prefix="summarize_") as tmp:
                await report("📥 下載字幕／音檔中（雲端備援）…")
                rc, out = await self._run_process(
                    [
                        *self._yt_dlp_base(),
                        "--skip-download",
                        "--write-subs",
                        "--sub-langs",
                        SUMMARY_LANG,
                        "--sub-format",
                        "vtt",
                        "--write-info-json",
                        "-o",
                        os.path.join(tmp, "subs.%(ext)s"),
                        url,
                    ],
                    timeout=180,
                )
                if rc != 0:
                    logger.warning("yt-dlp metadata/subs failed for %s: %s", url, out[-500:])
                    raise SummarizeError("無法取得影片資訊，影片可能不存在、未公開或尚未結束直播。")
                title = self._read_info_title(os.path.join(tmp, "subs.info.json"))

                for path in glob.glob(os.path.join(tmp, "subs.*.vtt")):
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        text = vtt_to_text(fh.read())
                    if text:
                        return {"title": title, "transcript": text, "source": "subs"}

                rc, out = await self._run_process(
                    [
                        *self._yt_dlp_base(),
                        "-f",
                        "bestaudio/best",
                        "-o",
                        os.path.join(tmp, "audio.%(ext)s"),
                        url,
                    ],
                    timeout=900,
                )
                audio = [
                    p for p in glob.glob(os.path.join(tmp, "audio.*"))
                    if not p.endswith((".part", ".ytdl"))
                ]
                if rc != 0 or not audio:
                    logger.warning("yt-dlp audio download failed for %s: %s", url, out[-500:])
                    raise SummarizeError("音檔下載失敗，請稍後再試。")

                # 16 kHz mono opus, split so each upload stays under Groq's limit.
                rc, out = await self._run_process(
                    [
                        "ffmpeg", "-y", "-loglevel", "error", "-i", audio[0],
                        "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "libopus", "-b:a", "32k",
                        "-f", "segment", "-segment_time", str(GROQ_CHUNK_SECONDS),
                        os.path.join(tmp, "chunk_%03d.ogg"),
                    ],
                    timeout=900,
                )
                chunks = sorted(glob.glob(os.path.join(tmp, "chunk_*.ogg")))
                if rc != 0 or not chunks:
                    logger.warning("ffmpeg transcode failed for %s: %s", url, out[-500:])
                    raise SummarizeError("音檔轉檔失敗，請稍後再試。")

                client = self._openai_client("groq")
                parts = []
                for i, chunk in enumerate(chunks, 1):
                    await report(f"☁️ 雲端轉錄中（{i}/{len(chunks)}）…")
                    try:
                        with open(chunk, "rb") as fh:
                            resp = await client.audio.transcriptions.create(
                                model=GROQ_TRANSCRIBE_MODEL,
                                file=fh,
                                language=SUMMARY_LANG,
                            )
                    except Exception as exc:
                        logger.exception("Groq transcription failed for %s", url)
                        raise SummarizeError("雲端轉錄失敗，請稍後再試。") from exc
                    parts.append((resp.text or "").strip())

        return {
            "title": title,
            "transcript": "\n".join(p for p in parts if p),
            "source": "groq",
            "transcribe_model": GROQ_TRANSCRIBE_MODEL,
        }

    @staticmethod
    def _read_info_title(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh).get("title")
        except (OSError, ValueError):
            return None

    # ---- summary ---------------------------------------------------------------

    def _openai_client(self, provider: str):
        """Lazily build (and cache) the OpenAI-compatible client for a provider."""
        from openai import AsyncOpenAI

        clients = self._summarize_clients
        if provider not in clients:
            if provider == "groq":
                clients[provider] = AsyncOpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
            else:
                clients[provider] = AsyncOpenAI(
                    api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL
                )
        return clients[provider]

    async def _summarize_transcript(self, title: str | None, transcript: str) -> str:
        if len(transcript) > SUMMARY_MAX_TRANSCRIPT_CHARS:
            transcript = transcript[:SUMMARY_MAX_TRANSCRIPT_CHARS] + "\n(以下省略)"
        user = f"タイトル: {title or '(不明)'}\n\n書き起こし:\n{transcript}"
        extra = (
            {"reasoning_effort": SUMMARY_REASONING_EFFORT}
            if SUMMARY_REASONING_EFFORT
            else {}
        )
        try:
            resp = await self._openai_client("openai").chat.completions.create(
                model=SUMMARY_MODEL,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                **extra,
            )
        except Exception as exc:
            logger.exception("Summary request failed")
            raise SummarizeError("產生摘要失敗，請稍後再試（逐字稿已快取）。") from exc
        summary = (resp.choices[0].message.content or "").strip()
        if not summary:
            raise SummarizeError("摘要模型沒有回傳內容，請稍後再試（逐字稿已快取）。")
        return summary
