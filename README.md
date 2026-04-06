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
- `/emoji_stats [guild_stats]`：查看表情符號使用統計
- `/scan_emoji_history [channel] [limit] [scan_guild] [unlimited]`：掃描歷史訊息統計表情符號使用（管理員專用）
- Twitter Profile 新推文推播到指定 Discord 頻道（可選）
- YouTube 社群貼文（Community Post）推播到指定 Discord 頻道（可選）

## 🧹 一次性清理 Bot 訊息

可使用 `cleanup_bot_messages.py` 刪除指定頻道中「本 Bot 帳號自己發送」的訊息。

```bash
python cleanup_bot_messages.py <channel_id_1> [channel_id_2 ...] [--limit N] [--max-delete N] [--dry-run]
```

範例：

```bash
# 先預覽會刪除幾則（不真的刪）
python cleanup_bot_messages.py 123456789012345678 --dry-run

# 刪除指定頻道中 bot 自己的訊息
python cleanup_bot_messages.py 123456789012345678

# 每個頻道最多掃描 2000 則歷史訊息，最多刪除 500 則
python cleanup_bot_messages.py 123456789012345678 --limit 2000 --max-delete 500
```

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
| `TWITTER_RATE_LIMIT_RESERVE`    | 低於此剩餘配額時先等 reset 再抓       | `2`    |
| `TWITTER_MEMORY_LIMIT`          | 每個帳號 dedupe 記憶上限              | `2000` |

Twitter 請求 endpoint：

`https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}`

### YouTube 社群貼文監控

| 變數                   | 說明                                            | 預設   |
| ---------------------- | ----------------------------------------------- | ------ |
| `YT_CHANNEL_IDS`       | 要監控的來源（Channel ID 或 @handle，逗號分隔） | 空     |
| `YT_NOTIFY_CHANNEL_ID` | 要推播到的 Discord 頻道 ID                      | 空     |
| `YT_POLL_INTERVAL`     | 輪詢間隔（秒）                                  | `60`   |
| `YT_MEMORY_LIMIT`      | 每個頻道 dedupe 記憶上限                        | `2000` |

範例 endpoint：

`{YT_API_BASE_URL}/channels?part=community&id={channel_id}`

`{YT_API_BASE_URL}/channels?part=community&handle=@SakuraMiko`

## 😊 表情符號統計功能

Bot 會在管理員執行掃描命令時統計表情符號使用情況，提供詳細的統計資訊。

### 指令說明

- `/emoji_stats [guild_stats=False]`：查看個人或伺服器表情符號使用統計
    - `guild_stats=False`：查看個人統計
    - `guild_stats=True`：查看整個伺服器的統計（管理員專用）
    - **注意**：統計資料來自管理員的掃描結果，不包含即時記錄

- `/scan_emoji_history [channel] [limit=1000] [scan_guild=False] [unlimited=False]`：掃描歷史訊息統計表情符號使用（管理員專用）
    - `channel`：要掃描的頻道（預設為當前頻道）
    - `limit`：每個頻道的掃描訊息數量上限（預設 1000）
    - `scan_guild`：是否掃描整個伺服器（預設 False）
    - `unlimited`：是否不限制訊息數量（僅對 scan_guild=True 有效，預設 False）
    - **注意**：掃描過程中會記錄表情符號使用情況到資料庫
- `/clear_emoji_stats`：清除所有表情符號統計資料（管理員專用）

### 支援的表情符號類型

- Unicode 表情符號（😀、👍、❤️ 等）
- Discord 自訂表情符號（靜態和動態）
