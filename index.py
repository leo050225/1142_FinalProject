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

# --- 1. 環境變數驗證 ---
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 初始化原生的 Gemini Client
client = genai.Client(api_key=GOOGLE_API_KEY)


# --- 2. 定義 Python 函式 (AI 工具庫) ---

def search_stock_id_by_name(name: str) -> str:
    """根據台灣公司的中文名稱或關鍵字（例如 '台積電'、'鴻海'、'富邦金'），查詢並找出對應的台灣股票代碼。

    Args:
        name: 公司的中文名稱或簡稱關鍵字。
    """
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockInfo"
    }
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if not data:
                return f"無法取得台灣股票清單。"
            
            # 模糊比對：尋找名稱包含關鍵字的股票
            matched_stocks = []
            for row in data:
                stock_name = row.get("stock_name", "")
                stock_id = row.get("stock_id", "")
                if name in stock_name:
                    matched_stocks.append(f"{stock_name}({stock_id})")
            
            if matched_stocks:
                return "找到相符的台灣股票對照如下：\n" + "\n".join(matched_stocks)
            else:
                return f"找不到名稱包含 '{name}' 的台灣股票。請檢查名稱是否正確。"
        return f"股票代碼查詢 API 連線失敗，狀態碼: {response.status_code}"
    except Exception as e:
        return f"呼叫股票代碼查詢 API 時發生異常: {str(e)}"


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

# 建立工具映射表 (新增了 search_stock_id_by_name)
tools_map = {
    "search_stock_id_by_name": search_stock_id_by_name,
    "get_historical_stock_data": get_historical_stock_data,
    "get_realtime_stock_snapshot": get_realtime_stock_snapshot
}


# --- 3. 核心邏輯：Gemini 原生 Function Calling 與 Line 回覆 ---
def process_gemini_and_reply(user_message, reply_token):
    final_answer = ""
    
    PRIMARY_MODEL = 'gemini-2.5-flash'
    FALLBACK_MODEL = 'gemini-1.5-flash'
    
    # 將新工具加入配置中
    config = types.GenerateContentConfig(
        system_instruction=(
            "妳是一個專業的台灣股市投資助手 Line 機器人。請務必使用繁體中文進行最終親切、扼要的回答。\n"
            "當使用者只提供公司名稱（例如：台積電、星宇航空、星巴克）時，妳必須先使用 `search_stock_id_by_name` 工具查出代碼，"
            "再根據使用者的意圖（查即時或歷史）去調用相對應的股價工具。"
        ),
        tools=[search_stock_id_by_name, get_historical_stock_data, get_realtime_stock_snapshot],
        temperature=0.1
    )

    def _call_gemini_with_retry(contents, max_retries=3, initial_delay=2):
        delay = initial_delay
        for attempt in range(max_retries):
            try:
                return client.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=contents,
                    config=config
                )
            except Exception as e:
                err_msg = str(e)
                if ("503" in err_msg or "UNAVAILABLE" in err_msg) and attempt < max_retries - 1:
                    print(f"[{PRIMARY_MODEL}] 遇到 503 過載，將於 {delay} 秒後進行第 {attempt + 1} 次重試...")
                    time.sleep(delay)
                    delay *= 2
                    continue
                else:
                    print(f"[{PRIMARY_MODEL}] 失敗，嘗試備援模型。錯誤: {err_msg}")
                    break
        try:
            print(f"啟動備援機制，改用模型: {FALLBACK_MODEL}")
            return client.models.generate_content(
                model=FALLBACK_MODEL,
                contents=contents,
                config=config
            )
        except Exception as fallback_err:
            raise fallback_err

    # 執行主邏輯
    try:
        # 第一輪對話
        response = _call_gemini_with_retry(contents=user_message)
        
        # 建立歷史對話結構，準備記錄多輪 Function Calling
        chats = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]
        
        # 使用 while 迴圈處理可能產生的「連續多個/多輪」Function Calls
        while response.function_calls:
            # 紀錄 AI 當下的決策（包含它想呼叫什麼工具）
            chats.append(response.candidates[0].content)
            
            tool_parts = []
            for function_call in response.function_calls:
                name = function_call.name
                args = function_call.args
                
                if name in tools_map:
                    # 執行工具
                    tool_result = tools_map[name](**args)
                    # 包裝成符合 SDK 規範的 Response
                    tool_parts.append(
                        types.Part.from_function_response(
                            name=name,
                            response={"output": str(tool_result)}
                        )
                    )
            
            # 將工具的執行結果塞回對話紀錄中
            if tool_parts:
                chats.append(types.Content(role="tool", parts=tool_parts))
            
            # 再次呼叫 Gemini，讓它根據剛剛的工具結果決定「下一步」
            # 如果是問「台積電股價」，這一輪 Gemini 就會拿到「2330」，並在此產生第二個 function_call（查即時股價）
            response = _call_gemini_with_retry(contents=chats)
        
        # 當沒有更多 function_calls 時，最後的 response.text 就是統整好的回答
        final_answer = response.text

    except Exception as e:
        print(f"Gemini Ultimate Error: {str(e)}")
        err_msg = str(e)
        if "503" in err_msg or "UNAVAILABLE" in err_msg:
            final_answer = "系統太熱門了！AI 伺服器目前有點忙不過來 \n請過幾秒鐘再對我發問一次試試看喔！"
        else:
            final_answer = "抱歉，我的大腦剛才開了一點小差，沒能成功取得資料 \n請您再試著重新輸入一次指令！"

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