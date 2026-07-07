"""Admin-triggered YouTube live-stream recording via yt-dlp.

An admin starts a recording with ``/record_start``; the bot spawns a yt-dlp
subprocess that writes the stream to ``RECORDING_OUTPUT_DIR`` on disk (persisted
to the host through the docker-compose bind-mount). ``/record_list`` shows
what's running and ``/record_stop`` ends a recording, letting yt-dlp finalize
the file gracefully (SIGINT).

Requires yt-dlp (see requirements.txt) and ffmpeg (installed in the image) for
muxing live streams. Recording state is kept in memory, so recordings stop if
the bot process restarts — including watchmedo's auto-reload on ``.py`` edits.
"""

import asyncio
import os
import re
import shutil
import signal
import sys
import time

from config import (
    RCLONE_CONFIG,
    RCLONE_PATH,
    RCLONE_REMOTE,
    RECORDING_MAX_CONCURRENT,
    RECORDING_OUTPUT_DIR,
    YT_DLP_PATH,
    logger,
)

# A bare YouTube video id, or a video id embedded in a common URL shape.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_VIDEO_ID_RE = re.compile(
    r"(?:v=|/live/|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})"
)


def normalize_target(target: str) -> tuple[str, str]:
    """Return ``(canonical_url, key)`` for a video id / URL / channel-live URL.

    ``key`` is the 11-char video id when we can find one (used as the handle
    for ``/record_stop`` and to dedupe), otherwise the raw target.
    """
    target = target.strip()
    if _VIDEO_ID_RE.match(target):
        return f"https://www.youtube.com/watch?v={target}", target

    key = target
    match = _URL_VIDEO_ID_RE.search(target)
    if match:
        key = match.group(1)

    if target.startswith("http"):
        url = target
    elif target.startswith("www.") or "youtube.com/" in target or "youtu.be/" in target:
        # Schemeless but already a full host (e.g. "www.youtube.com/...").
        url = f"https://{target}"
    else:
        # A youtube.com-relative path such as "@handle/live".
        url = f"https://www.youtube.com/{target.lstrip('/')}"
    return url, key


class RecordingMixin:
    # self.active_recordings: { key: {...} } and self._recording_available_cache
    # are initialized in MyBot.__init__.

    def recording_available(self) -> bool:
        """Whether yt-dlp is importable / on PATH (cached after first check)."""
        cached = getattr(self, "_recording_available_cache", None)
        if cached is not None:
            return cached
        if YT_DLP_PATH:
            ok = bool(shutil.which(YT_DLP_PATH)) or os.path.exists(YT_DLP_PATH)
        else:
            try:
                import yt_dlp  # noqa: F401

                ok = True
            except Exception:
                ok = False
        self._recording_available_cache = ok
        return ok

    def _yt_dlp_command(self) -> list[str]:
        if YT_DLP_PATH:
            return [YT_DLP_PATH]
        return [sys.executable, "-m", "yt_dlp"]

    def _reap_finished_recordings(self) -> None:
        """Drop entries whose subprocess has exited (and close their log)."""
        finished = [
            key
            for key, rec in self.active_recordings.items()
            if rec.get("process") is not None and rec["process"].returncode is not None
        ]
        for key in finished:
            rec = self.active_recordings.pop(key, None)
            if not rec:
                continue
            try:
                rec["log_fh"].close()
            except Exception:
                pass
            logger.info(
                "Recording finished: %s (rc=%s)", key, rec["process"].returncode
            )

    def list_recordings(self) -> list[dict]:
        self._reap_finished_recordings()
        return list(self.active_recordings.values())

    async def start_recording(
        self,
        target: str,
        from_start: bool,
        started_by: int,
        guild_id: int | None,
    ) -> tuple[bool, str, dict | None]:
        """Launch a yt-dlp recording. Returns ``(ok, error_message, record)``."""
        if not self.recording_available():
            return False, "錄影功能未啟用：找不到 yt-dlp（請確認已安裝相依套件）。", None

        self._reap_finished_recordings()
        if len(self.active_recordings) >= RECORDING_MAX_CONCURRENT:
            return (
                False,
                f"已達同時錄影上限（{RECORDING_MAX_CONCURRENT}），請先停止其他錄影。",
                None,
            )

        url, key = normalize_target(target)
        if key in self.active_recordings:
            return False, f"`{key}` 已經在錄影中了。", None

        os.makedirs(RECORDING_OUTPUT_DIR, exist_ok=True)
        started = int(time.time())
        output_template = os.path.join(
            RECORDING_OUTPUT_DIR, f"%(title)s-%(id)s-{started}.%(ext)s"
        )
        cmd = [
            *self._yt_dlp_command(),
            "--ignore-config",
            "--no-color",
            "--newline",
            "--no-playlist",
            "--restrict-filenames",
            "-o",
            output_template,
            "--live-from-start" if from_start else "--no-live-from-start",
            url,
        ]

        log_path = os.path.join(RECORDING_OUTPUT_DIR, f"record-{key}-{started}.log")
        try:
            log_fh = open(log_path, "wb")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            logger.exception("Failed to launch yt-dlp for %s", url)
            return False, "啟動 yt-dlp 失敗，請查看伺服器日誌。", None

        rec = {
            "key": key,
            "url": url,
            "process": process,
            "log_fh": log_fh,
            "log_path": log_path,
            "from_start": from_start,
            "started_by": started_by,
            "guild_id": guild_id,
            "started_at": started,
        }
        self.active_recordings[key] = rec

        # Grace period: catch an immediate exit (invalid URL / not a live video).
        try:
            rc = await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            logger.info(
                "Started recording %s (url=%s from_start=%s) by %d",
                key,
                url,
                from_start,
                started_by,
            )
            return True, "", rec

        # Exited within the grace period -> failed to start.
        self.active_recordings.pop(key, None)
        try:
            log_fh.close()
        except Exception:
            pass
        tail = self._read_log_tail(log_path)
        logger.warning("Recording %s exited immediately (rc=%s)", key, rc)
        message = "錄影隨即結束，可能不是進行中的直播或網址無效。"
        if tail:
            message += f"\n```\n{tail}\n```"
        return False, message, None

    async def stop_recording(self, key: str) -> dict | None:
        """Gracefully stop a recording so yt-dlp finalizes the output file.

        Returns the recording record (with an ``output_file`` key pointing at
        the saved media file, or None if it can't be located), or None if no
        active recording matches ``key``.
        """
        self._reap_finished_recordings()
        rec = self.active_recordings.get(key)
        if not rec:
            return None

        process = rec.get("process")
        if process is not None and process.returncode is None:
            try:
                process.send_signal(signal.SIGINT)  # let yt-dlp finalize the file
            except ProcessLookupError:
                pass
            except Exception:
                logger.exception("Error signaling recording %s; killing it", key)
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Recording %s did not stop in time; killing it", key)
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass

        try:
            rec["log_fh"].close()
        except Exception:
            pass
        self.active_recordings.pop(key, None)
        rec["output_file"] = self._find_output_file(key, rec.get("started_at"))
        logger.info("Stopped recording %s (file=%s)", key, rec.get("output_file"))
        return rec

    # ---- saved-file helpers --------------------------------------------------

    def _find_output_file(self, key: str, started_at) -> str | None:
        """Locate the media file a recording wrote (largest non-log match)."""
        marker = f"-{key}-{started_at}."
        try:
            names = os.listdir(RECORDING_OUTPUT_DIR)
        except OSError:
            return None
        candidates = []
        for name in names:
            if name.endswith(".log") or marker not in name:
                continue
            path = os.path.join(RECORDING_OUTPUT_DIR, name)
            try:
                candidates.append((os.path.getsize(path), path))
            except OSError:
                continue
        if not candidates:
            return None
        candidates.sort(reverse=True)  # largest first (the media, not fragments)
        return candidates[0][1]

    def list_saved_recordings(self) -> list[dict]:
        """List finished recordings on disk (name, size, mtime), newest first."""
        try:
            names = os.listdir(RECORDING_OUTPUT_DIR)
        except OSError:
            return []
        files = []
        for name in names:
            if name.endswith(".log"):
                continue
            path = os.path.join(RECORDING_OUTPUT_DIR, name)
            if not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            files.append(
                {"name": name, "size": st.st_size, "mtime": int(st.st_mtime)}
            )
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return files

    def safe_recording_path(self, filename: str) -> str | None:
        """Resolve a user-supplied filename to a file inside the recordings dir.

        Guards against path traversal: only a bare basename within
        RECORDING_OUTPUT_DIR is accepted.
        """
        base = os.path.basename(filename.strip())
        if not base or base.endswith(".log"):
            return None
        path = os.path.join(RECORDING_OUTPUT_DIR, base)
        root = os.path.realpath(RECORDING_OUTPUT_DIR)
        full = os.path.realpath(path)
        try:
            if os.path.commonpath([root, full]) != root:
                return None
        except ValueError:
            return None
        return full if os.path.isfile(full) else None

    # ---- cloud upload (rclone) -----------------------------------------------

    async def upload_recording(self, path: str) -> tuple[bool, str | None, str | None]:
        """Upload a finished recording to RCLONE_REMOTE via rclone.

        Returns ``(ok, share_link, error)``. rclone (not rsync) is used because
        it can target Google Drive et al. and mint a shareable link.
        """
        if not RCLONE_REMOTE:
            return False, None, "尚未設定 RCLONE_REMOTE"
        if not os.path.isfile(path):
            return False, None, "找不到檔案"

        base = os.path.basename(path)
        dest = RCLONE_REMOTE.rstrip("/") + "/" + base
        common = ["--config", RCLONE_CONFIG] if RCLONE_CONFIG else []

        rc, out = await self._run_process(
            [RCLONE_PATH, *common, "copyto", path, dest], timeout=None
        )
        if rc != 0:
            return False, None, (out or "rclone copy 失敗").strip()[-500:]

        rc, out = await self._run_process(
            [RCLONE_PATH, *common, "link", dest], timeout=120
        )
        link = None
        if rc == 0 and out.strip():
            link = out.strip().splitlines()[-1].strip()
        return True, link, None

    @staticmethod
    async def _run_process(cmd: list[str], timeout: float | None):
        """Run a subprocess, returning ``(returncode, combined_output_text)``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return 127, f"找不到執行檔：{cmd[0]}"
        except Exception as exc:  # noqa: BLE001
            return 1, str(exc)
        try:
            if timeout:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            else:
                out, _ = await proc.communicate()
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return 1, "逾時"
        return proc.returncode, (out or b"").decode("utf-8", "replace")

    @staticmethod
    def _read_log_tail(path: str, max_bytes: int = 600) -> str:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                data = fh.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""
        return "\n".join(data.splitlines()[-6:])
