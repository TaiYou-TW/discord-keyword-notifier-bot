FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg: required by yt-dlp to mux/remux recorded live streams.
# rclone: optional cloud upload of recordings (/record_upload) to Google Drive etc.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rclone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["watchmedo", "auto-restart", "--directory=.", "--pattern=*.py", "--recursive", "--", "python", "app.py"]