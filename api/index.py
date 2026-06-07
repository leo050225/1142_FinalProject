import os
import requests
import threading  # 引入線程庫，解決 Vercel / Line 超時問題
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

# --- 1. 環境變數驗證 ---
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# --- 2. 定義 LangChain Tools ---
@tool
def get_historical_stock_data(data_id: str, start_date: str, end_date: str) -> str:
    """
    查詢台灣股市的歷史或通用股票數據（例如每日收盤價、成交量等）。
    """
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": data_id,
        "start_date": start_date,
        "end_date": end_date
    }
    try:
        response = requests.get(url, params=params, timeout=4)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if not data:
                return f"找不到股票代碼 {data_id} 在該區間的歷史資料。"
            recent_data = data[-5:]
            result_str = f"成功取得 {data_id} 的歷史收盤資料（最近5筆）：\n"
            for row in recent_data:
                result_str += f"日期: {row.get('date')}, 收盤價: {row.get('close')}\n"
            return result_str
        return f"歷史 API 連線失敗，狀態碼: {response.status_code}"
    except Exception as e:
        return f"呼叫歷史股票 API 時發生異常: {str(e)}"


@tool
def get_realtime_stock_snapshot(data_id: str) -> str:
    """
    查詢台灣股市當前的即時盤中快照資訊（最新成交價、今日開盤等）。
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
            return (
                f"【即時快照】股票代碼: {data_id}\n"
                f"最新成交價: {snapshot.get('close', '無資料')}\n"
                f"今日開盤價: {snapshot.get('open', '無資料')}\n"
                f"今日最高價: {snapshot.get('high', '無資料')}\n"
                f"今日最低價: {snapshot.get('low', '無資料')}"
            )
        return f"即時快照 API 連線失敗，狀態碼: {response.status_code}"
    except Exception as e:
        return f"呼叫即時股票 API 時發生異常: {str(e)}"

tools = [get_historical_stock_data, get_realtime_stock_snapshot]


# --- 3. 初始化 LangChain Agent ---
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.1
)

prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "妳是一個專業的台灣股市投資助手 Line 機器人。妳擁有調用 FinMind 股票 API 工具的能力。\n"
        "請務必使用繁體中文進行最終親切、扼要的回答。\n\n"
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
    verbose=False,
    handle_parsing_errors=True
)


# --- 4. 核心邏輯：背景執行 Agent 並回覆 Line ---
def process_agent_and_reply(user_message, reply_token):
    try:
        # 在背景線程中跑 AI 運算
        agent_response = agent_executor.invoke({"input": user_message})
        final_answer = agent_response.get("output", "抱歉，我暫時無法解讀這個問題。")
    except Exception as e:
        print(f"Agent Error: {str(e)}")
        final_answer = "系統忙碌中，在分析數據時發生錯誤，請稍後再試。"

    # 運算完後才回傳給 Line 使用者
    try:
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=final_answer)]
                )
            )
    except Exception as line_err:
        print(f"Line Reply Error: {str(line_err)}")


# --- 5. Flask Webhook 路由接收端 ---
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
        
    return "OK"  # 快速回傳 OK 給 Line 伺服器，防止 500 錯誤


# --- 6. 監聽與處理 Line 訊息事件 ---
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    reply_token = event.reply_token
    
    # 【關鍵修改】不要在主線程等 AI 跑完！
    # 開啟新線程去跑 Agent，主線程立刻往下走並結束，讓 Flask 能一瞬間回傳 HTTP 200 "OK"
    threading.Thread(target=process_agent_and_reply, args=(user_message, reply_token)).start()