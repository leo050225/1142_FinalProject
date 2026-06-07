import os
import requests
import threading
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

# 移除 LangChain，改用 Google 官方原生的 google-genai SDK
# 請確保在 requirements.txt 中加入 google-genai
from google import genai
from google.genai import types

app = Flask(__name__)

# --- 1. 環境變數驗證 ---
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 初始化原生的 Gemini Client
client = genai.Client(api_key=GOOGLE_API_KEY)


# --- 2. 定義 Python 函式 (供 Gemini Function Calling 使用) ---

def get_historical_stock_data(data_id: str, start_date: str, end_date: str) -> str:
    """查詢台灣股市的歷史或通用股票數據（例如每日收盤價、成交量等）。

    Args:
        data_id: 股票代碼（例如 '2330'）。
        start_date: 開始日期，格式為 YYYY-MM-DD。
        end_date: 結束日期，格式為 YYYY-MM-DD。
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


def get_realtime_stock_snapshot(data_id: str) -> str:
    """查詢台灣股市當前的即時盤中快照資訊（最新成交價、今日開盤等）。

    Args:
        data_id: 股票代碼（例如 '2330'）。
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

# 建立工具映射表，方便後面動態呼叫
tools_map = {
    "get_historical_stock_data": get_historical_stock_data,
    "get_realtime_stock_snapshot": get_realtime_stock_snapshot
}


# --- 3. 核心邏輯：Gemini 原生 Function Calling 與 Line 回覆 ---
def process_gemini_and_reply(user_message, reply_token):
    final_answer = ""
    try:
        # 設定 System Instruction 與提供工具
        config = types.GenerateContentConfig(
            system_instruction="妳是一個專業的台灣股市投資助手 Line 機器人。請務必使用繁體中文進行最終親切、扼要的回答。",
            tools=[get_historical_stock_data, get_realtime_stock_snapshot],
            temperature=0.1
        )
        
        # 第一輪對話：讓 Gemini 決定是否需要呼叫工具
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=user_message,
            config=config
        )
        
        # 檢查 Gemini 是否發出 Function Call 請求
        if response.function_calls:
            # 建立對話紀錄，用於存放 Tool 執行的成果
            chats = [
                types.Content(role="user", parts=[types.Part.from_text(text=user_message)]),
                response.candidates[0].content # 包含 function_calls 的模型回應
            ]
            
            # 依序執行 Gemini 要求的所有 Function
            for function_call in response.function_calls:
                name = function_call.name
                args = function_call.args
                
                if name in tools_map:
                    # 執行對應的 Python 函式
                    tool_result = tools_map[name](**args)
                    
                    # 將工具執行的結果包裝成 Gemini 規範的格式
                    chats.append(
                        types.Content(
                            role="tool",
                            parts=[
                                types.Part.from_function_response(
                                    name=name,
                                    response={"result": tool_result}
                                )
                            ]
                        )
                    )
            
            # 第二輪對話：將工具結果丟回給 Gemini，讓它組織成最終的繁體中文回答
            final_response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=chats,
                config=config
            )
            final_answer = final_response.text
        else:
            # 如果不需呼叫工具，直接拿 Gemini 的回覆
            final_answer = response.text

    except Exception as e:
        print(f"Gemini Error: {str(e)}")
        final_answer = "系統忙碌中，在分析數據時發生錯誤，請稍後再試。"

    # 運算完後回傳給 Line 使用者
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


# --- 4. Flask Webhook 路由接收端 ---
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
    
    # 保持原有設計：開新線程去跑 Gemini 與 API 請求，立馬回傳 OK 給 Line，完美避免 500 / 超時問題
    threading.Thread(target=process_gemini_and_reply, args=(user_message, reply_token)).start()

# 為了讓 Vercel 能夠正確抓到 WSGI 實例，暴露 app 變數
app = app