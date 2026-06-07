
---

# 📈 台灣股市智慧查詢 LINE 機器人 (Gemini AI Agent)

這是一個結合 **Flask**、**LINE Bot SDK v3** 以及最新官方 **Google Genai SDK** 的智慧台股查詢 AI Agent 機器人。

透過 Gemini 的 **Multi-turn Function Calling (多輪工具調用)** 技術，機器人具備了強大的自主推理能力。無論使用者輸入的是股票代碼還是公司名稱（如：台積電、鴻海），AI 都能自動規劃步驟，先查出代碼，再獲取即時或歷史股價，並統整成親切的繁體中文回覆給使用者。

---

## 🚀 核心功能與特色

* **支援中文公司名查詢（全新升級）**：使用者不再需要死記 4 位數股票代碼！直接輸入「幫我查台積電股價」、「長榮航空最近表現如何」，AI 會自動進行模糊比對並找出對應代碼。
* **自主多輪推理鏈 (ReAct Tool Loop)**：當使用者問「聯發科今天開多少」時，AI 會自動拆解成兩階段執行：
1. 呼叫 `search_stock_id_by_name` 查出聯發科代碼為 `2454`。
2. 自動將 `2454` 帶入 `get_realtime_stock_snapshot` 查出盤中即時股價。


* **高可用性防禦機制（全新升級）**：
* **自動重試 (Exponential Backoff)**：遇到官方 Gemini 503 伺服器過載 (UNAVAILABLE) 時，程式會以指數型延遲自動進行最多 3 次重試，大幅提升穩定度。
* **雙模型備援方案**：若主要模型 (`gemini-2.5-flash`) 持續壅塞，系統會自動無縫切換至備援模型 (`gemini-1.5-flash`) 進行處理，確保服務不中斷。


* **即時盤中快照與歷史走勢**：串接 FinMind API，提供當日最新成交價、開/高/低價，或預設查詢近 30 天內的歷史收盤紀錄。
* **無縫相容 Serverless 環境**：程式碼改為同步處理，完美支援 **Vercel**、**Render** 或 **AWS Lambda** 等平台，100% 避免因 Function 提早結束而漏訊。

---

## 🛠️ 技術棧 (Tech Stack)

* **Python 3.10+**
* **Web 框架**：Flask
* **AI 模型與工具**：Google Genai SDK (`gemini-2.5-flash` / 備援 `gemini-1.5-flash`)
* **LINE 串接**：Line Bot SDK v3 (WebhookHandler, MessagingApi)
* **數據來源**：FinMind API (TaiwanStockInfo, TaiwanStockPrice, taiwan_stock_tick_snapshot)

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

## 🤖 系統運作架構 (以輸入「台積電股價」為例)

1. **使用者發送訊息**：在 LINE 聊天室發送：「幫我看看台積電股價」。
2. **LINE Webhook 轉發**：訊息傳送到 Flask 的 `/` 路由，經由 `WebhookHandler` 驗證簽章。
3. **Gemini 第一輪決策**：AI 判定使用者提供的是名稱，主動要求調用 `search_stock_id_by_name` 工具。
4. **執行代碼查詢**：程式至 FinMind API 撈取清單，模糊比對出「台積電(2330)」並回傳給 Gemini。
5. **Gemini 第二輪決策**：AI 拿到代碼 `2330`，發現還沒取得股價，再次發動 `get_realtime_stock_snapshot(data_id="2330")` 工具調用。
6. **執行股價查詢**：程式至 API 撈取 2330 的盤中快照數據並回傳。
7. **Gemini 最終統整**：AI 發現已取得完整資訊，將數據融入前後文，轉化為親切、扼要的繁體中文回答。
8. **回傳 LINE**：透過 `MessagingApi` 的 `reply_message` 發送給使用者。

---

## 📝 備註與優化

* **Tool 參數防呆防護**：自定義的 Python 函式皆加上了預設值參數（例如時間動態計算），防止 Gemini 在調用 Tool 時漏填參數導致程式報錯。
* **Serverless 運行優化**：移除了非同步的多執行緒 機制，確保在雲端 FaaS 平台上能完整等待 AI 推理與兩輪工具調用完畢後再行釋放，徹底告別漏訊問題。
* **使用者體驗優化**：當 AI 服務真的因官方問題（如嚴重過載）完全無法使用時，系統會自動拋出帶有貼圖/表情符號的友善提示，而非冷冰冰的程式錯誤碼。

---

## LineBot邀請連結
![LineBot邀請連結](images/linebot.png)