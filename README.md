
---

# 📈 台灣股市智慧查詢 LINE 機器人 (Gemini AI Agent)

這是一個結合 **Flask**、**LINE Bot SDK v3** 以及最新官方 **Google Genai SDK** 的智慧台股查詢機器人。

透過 Gemini 的 **Function Calling (工具調用)** 技術，機器人能夠自動識別使用者的意圖（例如：想查今天即時股價，或是想看歷史走勢），自動呼叫 FinMind 股市 API 獲取正確數據，再由 Gemini 統整成親切的繁體中文回答給 LINE 使用者。

---

## 🚀 核心功能與特色

* **自然語言語意辨識**：使用者不需要輸入死板的指令，直接輸入「幫我查 2330 今天開多少」、「台積電最近表現如何」即可查詢。
* **即時盤中快照**：串接 API 獲取當日最新成交價、開盤價、今日最高/最低價。
* **歷史走勢查詢**：預設查詢最近 30 天內的歷史收盤價，並智慧列出最新 5 筆紀錄。
* **最新官方 SDK 整合**：採用 Google 2025/2026 最新原生的 `google-genai` SDK，並配置 `gemini-2.5-flash` 模型，反應速度極快。
* **無縫相容 Serverless 環境**：程式碼移除了非同步的多執行緒 (`threading`)，改為同步處理，完美支援 **Vercel**、**Render** 或 **AWS Lambda** 等 Serverless 雲端平台，避免因提早結束 Function 而收不到回覆。

---

## 🛠️ 技術棧 (Tech Stack)

* **Python 3.10+**
* **Web 框架**：Flask
* **AI 模型與工具**：Google Genai SDK (`gemini-2.5-flash`)
* **LINE 串接**：Line Bot SDK v3 (WebhookHandler, MessagingApi)
* **數據來源**：FinMind API (TaiwanStockPrice, taiwan_stock_tick_snapshot)

---

## 📋 環境變數設定

在部署或本地運行此專案前，請務必在環境變數（或 `.env` 檔案）中設定以下金鑰：

| 環境變數名稱 | 說明 | 來源 |
| --- | --- | --- |
| `LINE_CHANNEL_SECRET` | LINE Bot 的 Channel Secret | LINE Developers Console |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Bot 的 Channel Access Token | LINE Developers Console |
| `GOOGLE_API_KEY` | Gemini AI 的 API Key | Google AI Studio |

---

## 📦 本地安裝與運行步驟

### 1. 複製專案與安裝依賴

```bash
# 安裝必備套件
pip install flask requests line-bot-sdk google-genai

```

### 2. 設定環境變數 (以 Linux/macOS 為例)

```bash
export LINE_CHANNEL_SECRET="你的_line_secret"
export LINE_CHANNEL_ACCESS_TOKEN="你的_line_token"
export GOOGLE_API_KEY="你的_gemini_api_key"

```

*(Windows 請使用 `set LINE_CHANNEL_SECRET="..."`)*

### 3. 啟動服務

```bash
python app.py

```

預設會在本地啟動 `http://127.0.0.1:5000`。請使用 `ngrok` 或直接部署至線上平台，並將網址填入 LINE Developer 填寫 Webhook URL (例如：`https://your-domain.com/`)。

---

## 🤖 系統運作架構

1. **使用者發送訊息**：在 LINE 聊天室發送：「幫我看看 2317 今天狀況」。
2. **LINE Webhook 轉發**：訊息傳送到 Flask 的 `/` 路由，經由 `WebhookHandler` 驗證簽章。
3. **Gemini 第一輪分析**：`gemini-2.5-flash` 收到訊息，判定需要調用 `get_realtime_stock_snapshot` 工具。
4. **執行 Python 函式**：程式自動至 FinMind API 撈取鴻海 (2317) 的即時快照數據。
5. **Gemini 第二輪統整**：AI 將 API 回傳的數據融入前後文，轉化為親切、扼要的繁體中文回答。
6. **回傳 LINE**：透過 `MessagingApi` 的 `reply_message` 發送給使用者。

---

## 📝 備註與優化

* **穩定性防護**：自定義的 Python 函式皆加上了預設值參數（例如時間動態計算），防止 Gemini 在調用 Tool 時漏填參數導致程式報錯。
* **Serverless 優化**：已將原先的異步 Thread 機制移除，確保在雲端 FaaS 平台上能完整等待 AI 運算完畢後再行釋放，100% 避免漏訊問題。

## 加入LineBot
![LINE Bot 畫面截圖](https://qr-official.line.me/sid/L/650tslqq.png)