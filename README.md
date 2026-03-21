# Keyword Notifier Bot

A simple Discord bot that notifies users when subscribed keywords appear in messages.

## 🚀 Start up

1. 複製 `.env.example` 為 `.env`，並填入你的 Discord Bot Token：

```bash
cp .env.example .env
# 編輯 .env，設定 DISCORD_TOKEN
```

```bash
docker compose up --build
```

## 🧩 功能

- `/notify_add <keyword>`：訂閱關鍵字
- `/notify_remove <keyword>`：取消訂閱
- `/notify_list`：查看已訂閱的關鍵字
- `/notify_cooldown <seconds>`：設定同一關鍵字通知冷卻時間
- Twitter Profile 新推文推播到指定 Discord 頻道（可選）

## 🔧 環境變數

| 變數               | 說明                                 | 預設          |
| ------------------ | ------------------------------------ | ------------- |
| `DISCORD_TOKEN`    | Discord Bot Token（必填）            | -             |
| `DB_PATH`          | SQLite 資料庫檔案位置                | `keywords.db` |
| `DEFAULT_COOLDOWN` | 預設的通知冷卻時間（秒）             | `30`          |
| `LOG_LEVEL`        | 日誌等級（DEBUG/INFO/WARNING/ERROR） | `INFO`        |

### Twitter 監控（Syndication API）

| 變數                            | 說明                                  | 預設   |
| ------------------------------- | ------------------------------------- | ------ |
| `TWITTER_SCREEN_NAMES`          | 要監控的帳號（逗號分隔）              | 空     |
| `TWITTER_NOTIFY_CHANNEL_ID`     | 要推播到的 Discord 頻道 ID            | 空     |
| `TWITTER_POLL_INTERVAL`         | 輪詢間隔（秒）                        | `60`   |
| `TWITTER_WORKER_COUNT`          | 並行 worker 數量                      | `4`    |
| `TWITTER_WAIT_BETWEEN_PROFILES` | 同一 worker 內每個 profile 間隔（秒） | `3`    |
| `TWITTER_WORKER_START_DELAY`    | worker 啟動錯開間隔（秒）             | `2`    |
| `TWITTER_MEMORY_LIMIT`          | 每個帳號 dedupe 記憶上限              | `2000` |

Twitter 請求 endpoint：

`https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}`
