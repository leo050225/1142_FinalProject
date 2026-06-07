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

# 🌟 [多帳號金鑰讀取優化]
# 你可以在環境變數設定 GOOGLE_API_KEY_1, GOOGLE_API_KEY_2, GOOGLE_API_KEY_3... 
# 程式會自動掃描並加入清單，如果都沒設定，則保底讀取原本的 GOOGLE_API_KEY
api_keys = []
idx = 1
while True:
    key = os.getenv(f"GOOGLE_API_KEY_{idx}")
    if key:
        api_keys.append(key)
        idx += 1
    else:
        break

# 如果沒有 GOOGLE_API_KEY_1 這種格式，就讀原本傳統的單一變數
if not api_keys:
    default_key = os.getenv("GOOGLE_API_KEY")
    if default_key:
        api_keys.append(default_key)

print(f"系統成功載入 {len(api_keys)} 組 Google API 金鑰，將啟用自動輪替配額防禦機制！")


# --- 2. 定義 Python 函式 (AI 工具庫) ---

def search_stock_id_by_name(name: str) -> str:
    """根據台灣公司的中文名稱或關鍵字（例如 '台積電'、'鴻海'），查詢並找出對應的台灣股票代碼。

    Args:
        name: 公司的中文名稱或簡稱關鍵字。
    """
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockInfo"}
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if not data:
                return "無法取得台灣股票清單。"
            
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

# 工具映射表
tools_map = {
    "search_stock_id_by_name": search_stock_id_by_name,
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
            "當使用者只提供公司名稱（例如：台積電、鴻海）時，妳必須先使用 `search_stock_id_by_name` 工具查出代碼，"
            "再根據使用者的意圖去調用相對應的股價工具。\n"
            "【重要限制】當所有工具（Tools）執行完畢並取得資料後，妳必須根據這些獲得的數據，組織成一段溫暖親切的繁體中文語句回答使用者，絕對不可回傳空白內容！"
        ),
        tools=[search_stock_id_by_name, get_historical_stock_data, get_realtime_stock_snapshot],
        temperature=0.1
    )

    # 🌟 [超級進化：多金鑰自動重試輪替輔助函式]
    def _call_gemini_with_key_loop(contents):
        """依序嘗試所有的 API 金鑰，若遇到 429 額度用盡，自動用下一組金鑰重新初始化 Client 繼續請求"""
        if not api_keys:
            raise Exception("環境變數中找不到任何有效的 GOOGLE_API_KEY。")
            
        err_msg = ""
        # 輪流嘗試每一組金鑰
        for k_idx, current_key in enumerate(api_keys):
            # 建立當前金鑰的臨時 Client
            temp_client = genai.Client(api_key=current_key)
            
            # 對於當前這個金鑰，同樣保有一套 503 繁忙自動重試機制 (最多重試 2 次)
            for attempt in range(2):
                try:
                    return temp_client.models.generate_content(
                        model=PRIMARY_MODEL,
                        contents=contents,
                        config=config
                    )
                except Exception as e:
                    err_msg = str(e)
                    
                    # 狀況 A：如果是 429 或是額度用完，重試無效，直接跳出 attempt 迴圈，換下一組 Key 試試看
                    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                        print(f"[Key 序號 {k_idx+1}] 觸發 429 額度上限。自動切換至下一組金鑰...")
                        break
                        
                    # 狀況 B：如果是 503 伺服器繁忙，稍微等一下再進行下一次 retry attempt
                    if ("503" in err_msg or "UNAVAILABLE" in err_msg) and attempt < 1:
                        time.sleep(2)
                        continue
                    else:
                        break # 其他未知的異常，直接切換下一個 Key 或者進入備援
                        
        # 🛡️ 防禦線 2：如果「所有金鑰」在主要模型 (2.5-flash) 下都回報 429 額度滿了，就改用備援模型 (2.0-flash) 重新跑一次 Key 迴圈
        print("所有配置的 API 金鑰在主要模型下皆已耗盡或異常，啟動二次防禦：嘗試備援模型。")
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
                continue # 如果這個 Key 在備援模型一樣不行，就再換下一個 Key
                
        # 如果走到這裡，代表所有 Key 在所有模型上都陣亡了，把最後的錯誤丟出去
        raise Exception(f"All keys exhausted. Last error: {err_msg}")

    # 執行主邏輯
    try:
        # 第一輪對話 (由多金鑰輪替函式全權處理)
        response = _call_gemini_with_key_loop(contents=user_message)
        
        # 建立歷史對話結構，準備記錄多輪 Function Calling
        chats = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]
        
        # 使用 while 迴圈處理多輪推理鏈工具調用
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
            
            # 再次呼叫變更後的多金鑰輪替函式
            response = _call_gemini_with_key_loop(contents=chats)
        
        # 檢查最後結果是否有效
        if response.text and response.text.strip():
            final_answer = response.text
        else:
            final_answer = "我已經成功幫您調用 API 獲取股價資訊，但剛才大腦組織文字時不小心落空了 😵。可以請您再對我說一次指令試試看嗎？"

    except Exception as e:
        print(f"Gemini Ultimate Error: {str(e)}")
        err_msg = str(e)
        
        # 如果連全線輪替都救不回來，說明真的全部額度在當天都乾涸了
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "All keys exhausted" in err_msg:
            final_answer = "今天小幫手背後配置的【所有免費帳號額度】都已經全數用光光了 😭。\n請明天再來發問，或者提醒主人幫我升級成綁定信用卡的付費制 API 唷！"
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