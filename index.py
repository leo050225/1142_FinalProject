import os
import time
import requests
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

# --- 1. 環境變數驗證與多金鑰載入 ---
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# [多帳號金鑰讀取]
# 自行在環境變數設定 GOOGLE_API_KEY_1, GOOGLE_API_KEY_2, GOOGLE_API_KEY_3...
api_keys = []
idx = 1
while True:
    key = os.getenv(f"GOOGLE_API_KEY_{idx}")
    if key:
        api_keys.append(key)
        idx += 1
    else:
        break

# 若無 GOOGLE_API_KEY_1 格式，則讀取傳統單一變數
if not api_keys:
    default_key = os.getenv("GOOGLE_API_KEY")
    if default_key:
        api_keys.append(default_key)

print(f"系統成功載入 {len(api_keys)} 組 Google API 金鑰，將啟用自動輪替配額防禦機制！")


# --- 2. 定義 Python 函式 (AI 工具庫) ---

def get_historical_stock_data(data_id: str, start_date: str = None, end_date: str = None) -> str:
    """查詢台灣股市的歷史或通用股票數據（例如每日收盤價、成交量等）。

    Args:
        data_id: 股票代碼（例如 '2330'）。
        start_date: 開始日期，格式為 YYYY-MM-DD。若未提供，預設為一個月前。
        end_date: 結束日期，格式為 YYYY-MM-DD。若未提供，預設為今天。
    """
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

# 工具映射表 (已移除名稱查詢)
tools_map = {
    "get_historical_stock_data": get_historical_stock_data,
    "get_realtime_stock_snapshot": get_realtime_stock_snapshot
}


# --- 3. 核心邏輯：Gemini 原生 Function Calling 與 Line 回覆 ---
def process_gemini_and_reply(user_message, reply_token):
    final_answer = ""
    
    PRIMARY_MODEL = 'gemini-2.5-flash'
    FALLBACK_MODEL = 'gemini-2.0-flash'  
    
    config = types.GenerateContentConfig(
        system_instruction=(
            "妳是一個專業的台灣股市投資助手 Line 機器人。請務必使用繁體中文進行最終親切、扼要的回答。\n"
            "使用者會提供 4 位數的台灣股票代碼（例如：2330、2317），請根據使用者的意圖去調用相對應的股價工具。\n"
            "【重要限制】當工具（Tools）執行完畢並取得資料後，妳必須根據這些獲得的數據，組織成一段溫暖親切的繁體中文語句回答使用者，絕對不可回傳空白內容！"
        ),
        tools=[get_historical_stock_data, get_realtime_stock_snapshot], # 已移除名稱查詢工具
        temperature=0.1
    )

    def _call_gemini_with_key_loop(contents):
        """依序嘗試所有的 API 金鑰，若遇到 429 額度用盡，自動用下一組金鑰重新初始化 Client 繼續請求"""
        if not api_keys:
            raise Exception("環境變數中找不到任何有效的 GOOGLE_API_KEY。")
            
        err_msg = ""
        for k_idx, current_key in enumerate(api_keys):
            temp_client = genai.Client(api_key=current_key)
            
            for attempt in range(2):
                try:
                    return temp_client.models.generate_content(
                        model=PRIMARY_MODEL,
                        contents=contents,
                        config=config
                    )
                except Exception as e:
                    err_msg = str(e)
                    
                    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                        print(f"[Key 序號 {k_idx+1}] 觸發 429 額度上限。自動切換至下一組金鑰...")
                        break
                        
                    if ("503" in err_msg or "UNAVAILABLE" in err_msg) and attempt < 1:
                        time.sleep(2)
                        continue
                    else:
                        break
                        
        print("所有配置的 API 金鑰在主要模型下皆已耗盡或異常，嘗試備援模型。")
        for k_idx, current_key in enumerate(api_keys):
            try:
                temp_client = genai.Client(api_key=current_key)
                return temp_client.models.generate_content(
                    model=FALLBACK_MODEL,
                    contents=contents,
                    config=config
                )
            except Exception as fallback_err:
                err_msg = str(fallback_err)
                continue
                
        raise Exception(f"All keys exhausted. Last error: {err_msg}")

    # 執行主邏輯
    try:
        response = _call_gemini_with_key_loop(contents=user_message)
        chats = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]
        
        while response.function_calls:
            chats.append(response.candidates[0].content)
            
            tool_parts = []
            for function_call in response.function_calls:
                name = function_call.name
                args = function_call.args
                
                if name in tools_map:
                    tool_result = tools_map[name](**args)
                    tool_parts.append(
                        types.Part.from_function_response(
                            name=name,
                            response={"output": str(tool_result)}
                        )
                    )
            
            if tool_parts:
                chats.append(types.Content(role="tool", parts=tool_parts))
            
            response = _call_gemini_with_key_loop(contents=chats)
        
        if response.text and response.text.strip():
            final_answer = response.text
        else:
            final_answer = "我已經成功幫您調用 API 獲取股價資訊，但剛才大腦組織文字時不小心落空了 😵。可以請您再對我說一次指令試試看嗎？"

    except Exception as e:
        print(f"Gemini Ultimate Error: {str(e)}")
        err_msg = str(e)
        
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "All keys exhausted" in err_msg:
            final_answer = "今天小幫手背後配置的【所有免費帳號額度】都已經全數用光光了 😭。\n請明天再來發問，或者提醒主人幫我升級成付費制 API 唷！"
        elif "503" in err_msg or "UNAVAILABLE" in err_msg:
            final_answer = "系統太熱門了！AI 伺服器目前有點忙不過來 🥵\n請過幾秒鐘再對我發問一次試試看喔！"
        else:
            final_answer = "抱歉，我的大腦剛才開了一點小差，沒能成功取得資料 🤯\n請您再試著重新輸入一次指令！"

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
    
    process_gemini_and_reply(user_message, reply_token)

app = app