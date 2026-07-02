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
- `/emoji_stats`：查看自己最常使用的表情符號與次數
- `/emoji_rank [top] [by_user]`：查看本伺服器表情符號使用排行榜（管理員專用）
- `/emoji_received_stats`：查看自己收到最多的表情回應（reaction）與次數
- `/emoji_received_rank [top]`：查看本伺服器收到最多表情回應的成員排行榜（管理員專用）
- `/scan_emoji_history [channel] [limit] [scan_guild] [unlimited]`：掃描歷史訊息統計表情符號使用（管理員專用）
- `/verify_membership`：連結 YouTube 帳號驗證頻道會員資格並取得會員身分組
- `/membership_status`：查看自己的會員驗證狀態
- `/membership_unlink`：解除連結並移除會員身分組
- `/membership_recheck`：立即重新驗證所有成員（管理員專用）
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

Bot 會即時記錄每則訊息與每個表情回應（reaction）中的表情符號使用情況，並提供詳細的統計資訊。管理員也可使用掃描命令補錄歷史訊息。

### 指令說明

- `/emoji_stats`：查看自己最常使用的表情符號排行榜（前 10 名）與總次數，並標示最愛的表情符號
- `/emoji_rank [top=10] [by_user=False] [publish=False]`：查看本伺服器表情符號使用排行榜（管理員專用）
    - `top`：顯示前幾名（1-25，預設 10）
    - `by_user=False`：依表情符號排序（哪些表情最常被使用）
    - `by_user=True`：依成員排序（哪些成員使用最多表情符號）
    - 統計**僅限當前伺服器**（依 `server_id` 區分，各伺服器獨立計算）

- `/emoji_received_stats [publish=False]`：查看自己收到最多的表情回應排行榜（前 10 名）與總次數，並標示最常收到的表情符號
    - 只計算 **reaction**（別人對你的訊息按的表情），不含訊息內文中的表情
    - 不計入自己對自己訊息的 reaction
    - 統計為跨伺服器的個人總計

- `/emoji_received_rank [top=10] [publish=False]`：查看本伺服器收到最多表情回應的成員排行榜（管理員專用）
    - 只計算 **reaction**（別人對成員訊息按的表情），不含訊息內文中的表情
    - 不計入自己對自己訊息的 reaction，也不計入對 Bot 訊息的 reaction
    - 統計**僅限當前伺服器**，各伺服器獨立計算

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

## 🔐 YouTube 會員驗證

讓成員用 Google 帳號授權，Bot 以**成員自己的授權**去讀取目標頻道的**會限影片**留言串來判斷會員資格（成功＝會員），不需要頻道擁有者授權。驗證通過即自動給予設定的 Discord 身分組，並定期重新檢查，失效時自動移除。

### 運作原理

1. 成員執行 `/verify_membership`，取得專屬 Google 授權連結（`youtube.readonly`）。
2. 授權後 Bot 儲存其 refresh token（以 Fernet 加密）。
3. 以該成員的權杖對頻道**會限影片**呼叫 `commentThreads.list`：`200`＝會員、`403`＝非會員。
4. 會限影片自動從「會員限定上傳」播放清單取得：把頻道 ID 的 `UC` 前綴換成 `UUMO`
   （例：`UCxxxx` → 播放清單 `UUMOxxxx`）。也可用 `MEMBERSHIP_PROBE_VIDEO_IDS` 手動指定。

### 設定步驟

1. **Google Cloud**：建立專案 → 啟用 *YouTube Data API v3* → 建立 OAuth 2.0「網頁應用程式」用戶端，
   將 `https://你的網域/oauth/callback` 加入授權重新導向 URI。
2. **OAuth 同意畫面**：`youtube.readonly` 屬敏感範圍。對外開放需經 Google 驗證（需隱私權政策與網域，可能耗時數週）；
   或維持「測試」模式（上限 100 人，但 **refresh token 每 7 天失效**，成員需每週重新授權）。
3. **反向代理**：將 `GOOGLE_OAUTH_REDIRECT_URI`（HTTPS）代理到容器的 `MEMBERSHIP_OAUTH_PORT`（預設 8081）。
   可直接使用範例設定 [`deploy/nginx-membership.conf.example`](deploy/nginx-membership.conf.example)（含 TLS 與 certbot 說明）。docker-compose 預設將此埠綁定在 `127.0.0.1`，僅供本機 nginx 存取。
4. 於 `.env` 填入 `GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI`、`MEMBERSHIP_GUILD_ID`、`MEMBERSHIP_ROLE_ID`、
   `MEMBERSHIP_YT_CHANNEL_ID`、`MEMBERSHIP_TOKEN_ENC_KEY`（用 `cryptography.fernet` 產生），
   建議另設 `YOUTUBE_API_KEY` 以穩定列出會限播放清單。詳見 `.env.example`。

### 注意事項

- **配額**：`commentThreads.list` 每次 1 unit，預設專案配額 10,000/日且所有成員共用，故 `MEMBERSHIP_CHECK_INTERVAL` 預設 6 小時，勿設太短。
- **會限影片前提**：探測影片必須「真的」是會限影片，否則非會員也會被判為會員（`UUMO` 播放清單即為會員限定上傳）。
- **權限**：Bot 需具備管理該身分組的權限，且身分組位階需低於 Bot 的最高身分組。
- Refresh token 以 `MEMBERSHIP_TOKEN_ENC_KEY` 加密儲存；請妥善保管此金鑰並提供隱私權政策。
