FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg: required by yt-dlp to mux/remux recorded live streams.
# rclone: optional cloud upload of recordings (/record_upload) to Google Drive etc.
# curl/unzip/ca-certificates: prerequisites for the Deno installer below
# (Deno ships as a zip, so unzip is required).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg rclone curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno: JS runtime yt-dlp uses to solve YouTube's signature/nsig challenges,
# which avoids download throttling and extraction errors on some videos.
# Installed to /usr/local/bin (already on PATH) via the official installer.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["watchmedo", "auto-restart", "--directory=.", "--pattern=*.py", "--recursive", "--", "python", "app.py"]