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
- `/verify_membership`：連結 YouTube 帳號（步驟 1，僅需一次）
- `/membership_link <channel>`：選擇你要驗證的會員頻道並取得身分組（步驟 2，可多次；支援自動完成）
- `/membership_unlink_channel <channel>`：從你的驗證清單移除某個頻道並移除身分組
- `/membership_status`：查看自己已選擇驗證的頻道與狀態
- `/membership_account`：查看自己目前連結的 YouTube 帳號
- `/membership_unlink`：完全解除連結並移除所有會員身分組
- `/membership_add <channel_id> <role>`：新增頻道與身分組對應（管理員專用）
- `/membership_remove <channel_id>`：移除頻道對應（管理員專用）
- `/membership_list`：列出所有頻道對應（管理員專用）
- `/membership_role_list`：列出本伺服器各會員身分組目前持有的成員（管理員專用）
- `/membership_recheck`：立即重新驗證所有成員（管理員專用）
- `/record_start <target> [from_start]`：開始錄製 YouTube 直播（管理員專用）
- `/record_stop <recording_id>`：停止指定的直播錄影（管理員專用）
- `/record_list`：列出進行中的直播錄影（管理員專用）
- `/record_files`：列出已錄製完成的檔案（管理員專用）
- `/record_upload <filename>`：把錄影檔上傳到雲端並貼出連結（rclone，管理員專用）
- `/record_delete <filename> [from_cloud]`：刪除錄影檔，可選擇一併從雲端刪除（管理員專用）
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

1. 成員執行 `/verify_membership`，取得專屬 Google 授權連結（`youtube.force-ssl`；讀取會限影片留言需要此範圍，`youtube.readonly` 會回傳 403 insufficientPermissions）。授權**只需一次**。
2. 授權後 Bot 儲存其 refresh token（以 Fernet 加密）。
3. 成員用 `/membership_link` **自行選擇**要驗證的頻道（可多次、支援自動完成，依身分組名稱搜尋）。Bot **只驗證成員選擇的頻道**，而非逐一檢查全部頻道 —— 這是控制 API 配額的關鍵。
4. 以該成員的權杖對所選頻道的**會限影片**呼叫 `commentThreads.list`：`200`＝會員、`403`＝非會員。
5. 會限影片自動從「會員限定上傳」播放清單取得：把頻道 ID 的 `UC` 前綴換成 `UUMO`
   （例：`UCxxxx` → 播放清單 `UUMOxxxx`），並將結果快取於資料庫，避免每次重列。

**多頻道、多伺服器**：授權是「以使用者為單位」，成員 `/verify_membership` **授權一次**後，於各伺服器用 `/membership_link` 選擇該伺服器要驗證的頻道即可。對應由各伺服器管理員以指令即時管理（`/membership_add`、`/membership_remove`、`/membership_list`），存於資料庫（可跨伺服器，同一頻道在不同伺服器可對應不同身分組），無需改設定或重啟。

> **升級相容**：既有已驗證的成員無需重做 —— 首次重新檢查時，Bot 會依成員**目前持有的會員身分組**自動補上對應的選擇，之後照常維持。

### 設定步驟

1. **Google Cloud**：建立專案 → 啟用 *YouTube Data API v3* → 建立 OAuth 2.0「網頁應用程式」用戶端，
   將 `https://你的網域/oauth/callback` 加入授權重新導向 URI。
2. **OAuth 同意畫面**：`youtube.force-ssl` 屬敏感範圍。對外開放需經 Google 驗證（需隱私權政策與網域，可能耗時數週）；
   或維持「測試」模式（上限 100 人，但 **refresh token 每 7 天失效**，成員需每週重新授權）。
   同意畫面所需的**應用程式首頁**、**隱私權政策**與**服務條款**頁面，Bot 已內建於 `static/`
   （`home.html` 說明用途、`privacy.html` 含 Google Limited Use 聲明），由回呼伺服器一併提供：
   應用程式首頁 `https://你的網域/`、隱私權政策 `https://你的網域/privacy.html`、服務條款 `https://你的網域/terms.html`。
   ⚠️ 同意畫面的**應用程式名稱必須與首頁一致**（本專案為 **BAUBAU Alert**）。**發布前請替換檔內所有 `[方括號]` 欄位。**
3. **反向代理**：將 `GOOGLE_OAUTH_REDIRECT_URI`（HTTPS）代理到容器的 `MEMBERSHIP_OAUTH_PORT`（預設 8081）。
   可直接使用範例設定 [`deploy/nginx-membership.conf.example`](deploy/nginx-membership.conf.example)（含 TLS 與 certbot 說明）。docker-compose 預設將此埠綁定在 `127.0.0.1`，僅供本機 nginx 存取。
4. 於 `.env` 填入 `GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI`、`MEMBERSHIP_TOKEN_ENC_KEY`
   （用 `cryptography.fernet` 產生），建議另設 `YOUTUBE_API_KEY` 以穩定列出會限播放清單。
   `MEMBERSHIP_GUILD_ID` 現為選用（僅用於自舊版單一伺服器設定升級時的資料轉移）。詳見 `.env.example`。
5. 啟動後由各伺服器管理員在該伺服器內以 `/membership_add <channel_id> <role>` 建立頻道與身分組的對應（可多個、可跨多個伺服器）。成員再各自用 `/membership_link` 選擇要驗證的頻道。

### 配額與擴充性

`commentThreads.list`／`playlistItems.list` 皆為每次 1 unit，預設專案配額為 **10,000/日**。因為驗證必須用「每位成員自己的權杖」逐一探測，成本會隨「成員數 × 頻道數」成長，所以本專案以下列方式壓低用量：

- **成員自選頻道（opt-in）**：只探測成員用 `/membership_link` 選擇的頻道，而非全部（一個掛 63 個頻道的大型伺服器，若每位成員只選自己有的 1～2 個，用量會從「成員數 × 63」降到「成員數 × 1～2」）。
- **只在需要時探測**：跳過成員未加入的伺服器所屬頻道。
- **每頻道只探測 1 部影片**（`MEMBERSHIP_MAX_PROBE_VIDEOS`）：會員第一次探測即為 `200`；多探測只會在非會員上多花配額。
- **每位成員最多每天重驗一次**（`MEMBERSHIP_RECHECK_MIN_INTERVAL`，預設 20h），且監控在達到 `MEMBERSHIP_DAILY_QUOTA`（預設 9000）時停止，隔日重置後續驗，避免直接觸頂報錯。
- **播放清單快取於資料庫**（`MEMBERSHIP_PROBE_TTL`，預設 24h），重啟後不需重列。

若你的伺服器規模仍會超過配額，可向 Google 申請提高 YouTube Data API 配額。

### 注意事項

- **會限影片前提**：探測影片必須「真的」是會限影片，否則非會員也會被判為會員（`UUMO` 播放清單即為會員限定上傳）。
- **權限**：Bot 需具備管理該身分組的權限，且身分組位階需低於 Bot 的最高身分組。
- Refresh token 以 `MEMBERSHIP_TOKEN_ENC_KEY` 加密儲存；請妥善保管此金鑰並提供隱私權政策。

## 🎥 YouTube 直播錄影

管理員可用指令請 Bot 以 [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) 錄製 YouTube 直播，
輸出檔案寫入 `RECORDING_OUTPUT_DIR`（預設 `recordings/`，透過 docker-compose 的 bind-mount
會出現在主機的專案目錄中）。

### 指令說明

- `/record_start <target> [from_start=False]`：開始錄製。`target` 可為 YouTube 影片網址或
  影片 ID（進行中的直播）；`from_start=True` 會嘗試從直播開頭錄製（需該直播開放 DVR）。
- `/record_stop <recording_id>`：停止錄影（送出 SIGINT 讓 yt-dlp 正常收尾並寫出檔案）。
  `recording_id` 即 `/record_list` 顯示的 ID（通常是 11 碼影片 ID）。停止後會回報檔名、
  大小與可用的分享方式。
- `/record_list`：列出進行中的錄影、開始時間與來源網址。
- `/record_files`：列出錄影資料夾中已完成的檔案（檔名、大小、時間）。
- `/record_delete <filename> [from_cloud=False]`：刪除錄影檔；`from_cloud=True` 會一併從雲端刪除。

### 分享錄影

直播錄影檔通常很大，Discord 的上傳大小限制（一般 25 MB）多半放不下，因此以雲端／主機資料夾分享為主：

- **`/record_upload <filename>`**：用 [`rclone`](https://rclone.org/) 上傳到**雲端**（如 Google Drive），
  取得分享連結後由 Bot 貼到頻道。適合大型檔案。
- 或直接從**主機**的錄影資料夾（`RECORDING_HOST_DIR`，預設 `./recordings`）取用檔案，自行上傳／分享。

> 為什麼是 rclone 而不是 rsync？`rsync` 是走 SSH 同步到「另一台伺服器」，本身不會上傳到 Google Drive；
> `rclone` 原生支援 Google Drive 等雲端空間，且能產生分享連結。若你想同步到自有伺服器再用網址分享，
> `rsync`／`scp` 也可以，但那需要你自架檔案伺服器。

啟用雲端上傳（Google Drive 範例）：

1. 在主機上執行一次 `rclone config` 建立遠端（例如命名為 `gdrive` 的 Google Drive 遠端），產生 `rclone.conf`。
2. 依 `docker-compose.yml` 內的註解，把該 `rclone.conf` 掛載進容器，並設定 `RCLONE_CONFIG` 指向它。
3. 在 `.env` 設定 `RCLONE_REMOTE=gdrive:vtuber-recordings`（`<遠端>:<資料夾>`）。

### 刪除與自動清理

- **手動刪除**：`/record_delete <filename>` 刪除主機上的檔案；加上 `from_cloud=True` 會同時用 rclone
  從雲端刪除同名檔案。
- **自動清理**：Bot 每 6 小時清一次，刪除超過 `RECORDING_RETENTION_DAYS`（預設 7 天）的錄影檔，
  設為 `0` 可停用。**只會刪除主機上的檔案，不會動到已上傳到雲端的副本**；進行中的錄影檔不會被清除。

### 注意事項

- **相依套件**：需要 `yt-dlp`（已列於 `requirements.txt`）、`ffmpeg`、`rclone` 與 `deno`（皆已在 Docker image 內安裝；
  `deno` 供 yt-dlp 解 YouTube 簽章／nsig 挑戰，避免下載被限速或解析失敗）。
  修改 `requirements.txt` 或 `Dockerfile` 後需 `docker compose up -d --build`。
- **資料夾綁定**：`docker-compose.yml` 已把主機的 `RECORDING_HOST_DIR`（預設 `./recordings`）綁定到容器的
  `/app/recordings`，錄影檔會直接出現在主機上。
- **狀態不持久**：進行中的錄影只存在記憶體中。Bot 行程重啟（含 watchmedo 偵測到 `.py` 變更
  自動重載、或容器重啟）會中斷所有錄影，已寫入的檔案仍會保留。
- **同時上限**：由 `RECORDING_MAX_CONCURRENT` 控制（預設 3）。
- **磁碟空間**：直播錄影檔案可能很大，請留意 `RECORDING_HOST_DIR` 所在磁碟的容量。
