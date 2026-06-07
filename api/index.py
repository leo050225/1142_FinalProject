import os
import requests
from flask import Flask, request, abort

# Line 本地 SDK v3 引用
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# LangChain 核心與工具引用
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_structured_chat_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

app = Flask(__name__)

# --- 1. 環境變數驗證（Vercel 環境下不需要 load_dotenv，會直接從後台讀取） ---
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 初始化 Line Bot 核心配置
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# --- 2. 定義 LangChain Tools (FinMind 股票 API 封裝) ---

@tool
def get_historical_stock_data(data_id: str, start_date: str, end_date: str) -> str:
    """
    查詢台灣股市的歷史或通用股票數據（例如每日收盤價、成交量、開盤價等）。
    
    參數:
    - data_id: 股票代碼 (例如 '2330')
    - start_date: 開始日期，格式為 YYYY-MM-DD (例如 '2026-01-01')
    - end_date: 結束日期，格式為 YYYY-MM-DD (例如 '2026-06-01')
    """
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": data_id,
        "start_date": start_date,
        "end_date": end_date
    }
    try:
        # 將 timeout 縮短至 4 秒，防止 FinMind 伺服器太慢導致 Vercel 整體超時
        response = requests.get(url, params=params, timeout=4)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if not data:
                return f"找不到股票代碼 {data_id} 在該區間的歷史資料。"
            
            recent_data = data[-5:]
            result_str = f"成功取得 {data_id} 的歷史收盤資料（僅列出最近5筆）：\n"
            for row in recent_data:
                result_str += f"日期: {row.get('date')}, 收盤價: {row.get('close')}, 開盤價: {row.get('open')}, 最高: {row.get('max')}, 最低: {row.get('min')}, 成交量: {row.get('trading_volume')}\n"
            return result_str
        return f"歷史 API 連線失敗，狀態碼: {response.status_code}"
    except Exception as e:
        return f"呼叫歷史股票 API 時發生異常: {str(e)}"


@tool
def get_realtime_stock_snapshot(data_id: str) -> str:
    """
    查詢台灣股市當前的即時盤中快照資訊（例如最新成交價、今日開盤、最高、最低價、今日累計成交量等）。
    適用於用戶詢問「現在這檔股票多少錢」、「今天走勢如何」、「即時行情」等現況問題。
    
    參數:
    - data_id: 股票代碼 (例如 '2330')
    """
    url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
    params = {"data_id": data_id}
    try:
        response = requests.get(url, params=params, timeout=4)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if not data:
                return f"找不到股票代碼 {data_id} 的即時快照資料。"
            
            snapshot = data[0] if isinstance(data, list) and len(data) > 0 else data
            
            close = snapshot.get("close", "無資料")
            open_p = snapshot.get("open", "無資料")
            high = snapshot.get("high", "無資料")
            low = snapshot.get("low", "無資料")
            volume = snapshot.get("volume", "無資料")
            
            return (
                f"【即時快照結果】股票代碼: {data_id}\n"
                f"最新成交價: {close}\n"
                f"今日開盤價: {open_p}\n"
                f"今日最高價: {high}\n"
                f"今日最低價: {low}\n"
                f"今日成交量: {volume}"
            )
        return f"即時快照 API 連線失敗，狀態碼: {response.status_code}"
    except Exception as e:
        return f"呼叫即時股票 API 時發生異常: {str(e)}"

tools = [get_historical_stock_data, get_realtime_stock_snapshot]


# --- 3. 初始化 LangChain Agent 核心 ---
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.1  # 稍微再調低一點點，加快 Gemini 輸出 JSON 的果斷度
)

prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "妳是一個專業的台灣股市投資助手 Line 機器人。妳擁有調用 FinMind 股票 API 工具的能力。\n"
        "當用戶問及股票行情時，請根據對話的語意判斷調用合適的工具：\n"
        "- 當用戶詢問「現在/今天/目前的價格或走勢」，請調用 `get_realtime_stock_snapshot`。\n"
        "- 當用戶詢問「過去一段時間/歷史表現/某月某日行情」，請調用 `get_historical_stock_data`（如果用戶沒提及日期區間，預設帶入最近一個月的範圍）。\n\n"
        "請務必使用繁體中文進行最終親切、扼要的回答。若用戶詢問與財經、台股無關的話題，請禮貌拒絕。\n\n"
        "妳必須且只能使用以下工具來協助妳回答問題：\n"
        "{tools}\n\n"
        "【重要】工具呼叫格式規範：妳必須以 Markdown JSON 程式碼區塊回應，結構如下：\n"
        "```json\n"
        "{{\n"
        "  \"action\": \"工具名稱\",\n"
        "  \"action_input\": {{\n"
        "    \"參數名\": \"參數值\"\n"
        "  }}\n"
        "}}\n"
        "```\n"
        "當妳取得工具回傳的結果，準備直接回答用戶時，妳的最終回應必須嚴格符合以下 JSON 格式：\n"
        "```json\n"
        "{{\n"
        "  \"action\": \"Final Answer\",\n"
        "  \"action_input\": \"這裡輸入妳要呈現在 Line 畫面上給用戶看的最終繁體中文回答內容。\"\n"
        "  }}\n"
        "}}\n"
        "```"
    )),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}\n\n{agent_scratchpad}")
])

agent = create_structured_chat_agent(llm, tools, prompt_template)
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=False,               # 在雲端生產環境建議關閉 verbose 減少日誌負擔
    handle_parsing_errors=True
)


# --- 4. Flask Webhook 路由接收端 ---
# Vercel 呼叫此檔案時預設會指向 /api/index，配合 vercel.json 轉發即可

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return "Line Bot 運行中！"
        
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# --- 5. 監聽與處理 Line 訊息事件 ---

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    reply_token = event.reply_token
    
    try:
        # 執行 Agent
        agent_response = agent_executor.invoke({"input": user_message})
        final_answer = agent_response.get("output", "抱歉，我暫時無法解讀這個問題。")
    except Exception as e:
        print(f"Agent Error: {str(e)}")
        final_answer = "系統忙碌中，在為您分析股票數據時發生錯誤，請稍後再試。"

    # 回傳給 Line 使用者
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=final_answer)]
            )
        )