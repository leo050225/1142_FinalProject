
---

# 📈 台灣股市智慧查詢 LINE 機器人 (Gemini AI Agent)

這是一個結合 **Flask**、**LINE Bot SDK v3** 以及最新官方 **Google Genai SDK** 的智慧台股查詢 AI Agent 機器人。

透過 Gemini 的 **Function Calling (工具調用)** 技術，機器人具備了自主推理能力。使用者只要輸入 **4 位數台灣股票代碼**，AI 就能自動判斷使用者的意圖，自主選擇調用即時盤中快照或歷史走勢工具，並統整成親切的繁體中文回覆給使用者。

---

## 🚀 核心功能與特色

* **多金鑰自動輪替配額防禦（全新升級）**：
* 支援在環境變數中設定多組 Google API 金鑰（`GOOGLE_API_KEY_1`, `GOOGLE_API_KEY_2`...）。
* 系統遭遇 `429 RESOURCE_EXHAUSTED`（額度用盡）時，會**自動切換至下一組金鑰**重新嘗試，大幅提升免費帳號的耐用度。


* **高可用性與雙模型備援方案**：
* **自動重試機制**：遇到官方 Gemini `503 UNAVAILABLE`（伺服器過載）時，程式會自動延遲 2 秒並進行重試。
* **雙模型備援**：若所有金鑰在主要模型（`gemini-2.5-flash`）下皆無法提供服務，系統會自動切換至備援模型（`gemini-2.0-flash`），確保服務不中斷。


* **精準的股價工具鏈**：
* **即時盤中快照**：提供當日最新成交價、開盤價、最高價、最低價。
* **歷史走勢查詢**：動態計算時間，預設查閱最近 5 筆歷史收盤紀錄。


* **無縫相容 Serverless 環境**：
* 採用同步阻斷式設計，完美支援 **Vercel**、**Render** 或 **AWS Lambda** 等平台，100% 避免因輕量化雲端環境提早結束 Function 而導致的 LINE 漏訊問題。



---

## 🛠️ 技術棧 (Tech Stack)

* **Python 3.10+**
* **Web 框架**：Flask
* **AI 模型與工具**：Google Genai SDK (`gemini-2.5-flash` / 備援 `gemini-2.0-flash`)
* **LINE 串接**：Line Bot SDK v3 (WebhookHandler, MessagingApi)
* **數據來源**：FinMind API (TaiwanStockPrice, taiwan_stock_tick_snapshot)

---

## 📋 環境變數設定

在部署或本地運行此專案前，請務必在環境變數中設定以下金鑰。若要啟用**金鑰輪替機制**，請依序設定帶有數字後綴的 Google 金鑰：

| 環境變數名稱 | 說明 | 來源 |
| --- | --- | --- |
| `LINE_CHANNEL_SECRET` | LINE Bot 的 Channel Secret | LINE Developers Console |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Bot 的 Channel Access Token | LINE Developers Console |
| `GOOGLE_API_KEY_1` | 第一組 Gemini AI API Key (主要) | Google AI Studio |
| `GOOGLE_API_KEY_2` | 第二組 Gemini AI API Key (輪替備援) | Google AI Studio |
| `GOOGLE_API_KEY` | 傳統單一金鑰 (若未設定 `_1` 格式時的防呆備用) | Google AI Studio |

---

## 📦 本地安裝與運行步驟

### 1. 安裝依賴套件

```bash
pip install flask requests line-bot-sdk google-genai
```

### 2. 設定環境變數 (以 Linux/macOS 為例)

```bash
export LINE_CHANNEL_SECRET="你的_line_secret"
export LINE_CHANNEL_ACCESS_TOKEN="你的_line_token"
# 可設定多組金鑰
export GOOGLE_API_KEY_1="第一組_gemini_key"
export GOOGLE_API_KEY_2="第二組_gemini_key"
```

*(Windows 請使用 `set LINE_CHANNEL_SECRET="..."`)*

### 3. 啟動服務

```bash
python app.py
```

預設會在本地啟動 `http://127.0.0.1:5000`。請使用 `ngrok` 對外映射，或直接部署至線上平台，並將網址填入 LINE Developer 的 Webhook URL (例如：`https://your-domain.com/`)。

---

## 🤖 系統運作架構 (以輸入「2330即時股價」為例)

1. **使用者發送訊息**：在 LINE 聊天室發送：「幫我看看 2330 即時股價」。
2. **LINE Webhook 轉發**：訊息傳送到 Flask 的 `/` 路由，經由 `WebhookHandler` 驗證簽章。
3. **Gemini 第一輪決策**：AI 辨識出 4 位數股票代碼，並根據意圖主動要求調用 `get_realtime_stock_snapshot(data_id="2330")`。
4. **執行工具 (Tool Execution)**：程式至 FinMind API 撈取 2330 的盤中快照數據，並將結果送回給 Gemini。
5. **Gemini 最終統整**：AI 獲得數據後，根據 `system_instruction` 融入前後文，轉化為溫暖親切、扼要的繁體中文答案（絕不回傳空白）。
6. **回傳 LINE**：透過 `MessagingApi` 的 `reply_message` 發送給使用者。

> ⚠️ **注意**：本版本已移除「公司名稱查詢」功能，使用者**必須提供 4 位數股票代碼**（例如：2330、2317），AI 才能正確調用工具。

---

## 📝 備註與例外處理優化

* **429 額度耗盡防護**：若所有配置的金鑰都觸發頻率限制，AI 會自動回覆友善的提示語，引導使用者明日再試或提醒管理員升級付費制，避免程式崩潰。
* **空白回覆防呆**：當 AI 成功調用工具卻在組織文字時落空，系統設有底線防護機制，會主動發送導引訊息，提供最佳的使用者體驗。

---