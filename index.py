import os
import requests
import threading
from datetime import datetime, timedelta
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

# 使用 Google 官方原生的 google-genai SDK
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


# --- 2. 定義 Python 函式 (加上預設值，避免 Gemini 沒填參數而崩潰) ---

def get_historical_stock_data(data_id: str, start_date: str = None, end_date: str = None) -> str:
    """查詢台灣股市的歷史或通用股票數據（例如每日收盤價、成交量等）。

    Args:
        data_id: 股票代碼（例如 '2330'）。
        start_date: 開始日期，格式為 YYYY-MM-DD。若未提供，預設為一個月前。
        end_date: 結束日期，格式為 YYYY-MM-DD。若未提供，預設為今天。
    """
    # 動態計算預設時間
    today = datetime.today()
    if not end_date:
        end_date = today.strftime('%Y-%m-%d')
    if not start_date:
        start_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')

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
                return f"找不到股票代碼 {data_id} 在 {start_date} 到 {end_date} 區間的歷史資料。"
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

# 建立工具映射表
tools_map = {
    "get_historical_stock_data": get_historical_stock_data,
    "get_realtime_stock_snapshot": get_realtime_stock_snapshot
}


# --- 3. 核心邏輯：Gemini 原生 Function Calling 與 Line 回覆 ---
def process_gemini_and_reply(user_message, reply_token):
    final_answer = ""
    
    target_model = 'gemini-2.5-flash' 
    
    try:
        config = types.GenerateContentConfig(
            system_instruction="妳是一個專業的台灣股市投資助手 Line 機器人。請務必使用繁體中文進行最終親切、扼要的回答。",
            tools=[get_historical_stock_data, get_realtime_stock_snapshot],
            temperature=0.1
        )
        
        # 第一輪對話
        response = client.models.generate_content(
            model=target_model,
            contents=user_message,
            config=config
        )
        
        # 檢查是否需要執行 Function Call
        if response.function_calls:
            chats = [
                types.Content(role="user", parts=[types.Part.from_text(text=user_message)]),
                response.candidates[0].content 
            ]
            
            for function_call in response.function_calls:
                name = function_call.name
                args = function_call.args
                
                if name in tools_map:
                    # 執行 Python 函式取得字串結果
                    tool_result = tools_map[name](**args)
                    
                    # 修正：將原生字串包裝成符合 SDK 規範的 Response 結構
                    chats.append(
                        types.Content(
                            role="tool",
                            parts=[
                                types.Part.from_function_response(
                                    name=name,
                                    response={"output": str(tool_result)} # 修正字典結構
                                )
                            ]
                        )
                    )
            
            # 第二輪對話
            final_response = client.models.generate_content(
                model=target_model,
                contents=chats,
                config=config
            )
            final_answer = final_response.text
        else:
            final_answer = response.text

    except Exception as e:
        print(f"Gemini Error: {str(e)}")
        final_answer = f"系統忙碌中，錯誤回報：{str(e)}"

    # 回傳給 LINE 使用者
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
    
    # 🔴 移除原本的 threading.Thread，改為直接同步呼叫
    # 這樣 Vercel 就會乖乖等 Gemini 發出對外請求並等回覆完畢後，才結束這次的 Function
    process_gemini_and_reply(user_message, reply_token)
app = app