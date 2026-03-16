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

## 🔧 環境變數

| 變數 | 說明 | 預設 |
|------|------|------|
| `DISCORD_TOKEN` | Discord Bot Token（必填） | - |
| `DB_PATH` | SQLite 資料庫檔案位置 | `keywords.db` |
| `DEFAULT_COOLDOWN` | 預設的通知冷卻時間（秒） | `30` |
| `LOG_LEVEL` | 日誌等級（DEBUG/INFO/WARNING/ERROR） | `INFO` |
