import streamlit as st
import google.generativeai as genai
import os
import glob
import threading
import requests
import time

# 設定網頁標題與圖示
st.set_page_config(page_title="🤖 HR 規章問答機器人", page_icon="📝", layout="wide")

st.title("🤖 HR 規章問答機器人")
st.write("這是一個專為員工設計的問答系統，您可以針對公司的規章制度提出問題。系統會自動根據最新版的員工守則或規章回覆您。")

# 準備讀取規章
knowledge_base = ""
rules_dir = "規章"
rules_files = []

# 確認規章資料夾存在
if os.path.exists(rules_dir):
    # 尋找所有 markdown 和 txt 檔案
    rules_files = glob.glob(os.path.join(rules_dir, "*.md")) + glob.glob(os.path.join(rules_dir, "*.txt"))
    
    for f in rules_files:
        try:
            with open(f, "r", encoding="utf-8") as file:
                knowledge_base += f"\n\n--- 檔案：{os.path.basename(f)} ---\n"
                knowledge_base += file.read()
        except Exception as e:
            st.error(f"讀取 {f} 失敗：{e}")

# 優先嘗試從 Streamlit Secrets 後台讀取 API Key (部署到雲端後使用)
api_key = ""
try:
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

# 只有在「沒有抓到後台金鑰」時，才顯示左側選單（供本地測試或管理員除錯用）
if not api_key:
    with st.sidebar:
        st.header("⚙️ 系統設定")
        st.warning("尚未在後台設定 API Key，請在下方手動輸入（或前往後台設定）。")
        api_key = st.text_input("輸入您的 Google Gemini API Key", type="password", help="這裡輸入的 Key 重新整理後會消失。若要永久保存，請在 Streamlit 後台設定 Secrets。")
        st.markdown("[👉 點此免費取得 Gemini API Key](https://aistudio.google.com/app/apikey)")
        
        st.markdown("---")
        st.markdown("### 📚 知識庫狀態")
        
        if rules_files:
            st.success(f"✅ 已成功載入 {len(rules_files)} 份規章檔案。")
            for f in rules_files:
                file_name = os.path.basename(f)
                st.markdown(f"- `{file_name}`")
        else:
            st.error(f"❌ 找不到任何規章檔案。請將 `.md` 或 `.txt` 檔案放入 `{rules_dir}` 資料夾中。")

# ----------------- 防休眠 (Keep-Alive) 機制 -----------------
def keep_alive(url):
    """每 15 分鐘向指定的 URL 發送請求以防休眠"""
    while True:
        try:
            requests.get(url)
        except Exception:
            pass
        time.sleep(15 * 60)

# 從 Secrets 嘗試取得 App 網址
app_url = ""
try:
    if "APP_URL" in st.secrets:
        app_url = st.secrets["APP_URL"]
except Exception:
    pass

# 如果沒有抓到後台設定，讓使用者可以在側邊欄輸入
if not app_url:
    with st.sidebar:
        st.markdown("---")
        st.markdown("### ⏰ 防休眠機制 (Keep-Alive)")
        st.info("若系統部署於雲端，一段時間無人使用會自動休眠。請輸入網址讓系統定期自我喚醒。")
        app_url = st.text_input("輸入此 App 公開網址", placeholder="例如：https://hr-bot.streamlit.app")

# 啟動背景防休眠執行緒
if app_url and "keep_alive_thread" not in st.session_state:
    t = threading.Thread(target=keep_alive, args=(app_url,), daemon=True)
    t.start()
    st.session_state.keep_alive_thread = True
# --------------------------------------------------------

# 初始化聊天紀錄
if "messages" not in st.session_state:
    # 預設歡迎訊息
    st.session_state.messages = [{"role": "assistant", "content": "您好！我是 HR 的 AI 小助手。關於公司的休假制度或其他規章，您有什麼想了解的嗎？例如：「請問病假可以請幾天？」"}]

# 顯示聊天紀錄
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 處理使用者輸入
if prompt := st.chat_input("請在此輸入您的問題..."):
    # 檢查 API Key 狀態
    if not api_key:
        st.warning("⚠️ 請先在左側選單輸入您的「Google Gemini API Key」才能開始對話喔！")
        st.stop()
        
    # 檢查知識庫狀態
    if not knowledge_base:
        st.error("⚠️ 找不到任何規章檔案作為知識庫，機器人目前無法回答問題。")
        st.stop()

    # 顯示使用者訊息
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 準備呼叫 API
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        try:
            # 設定 Gemini API
            genai.configure(api_key=api_key)
            
            # 動態尋找這個 API Key 有權限使用的模型，藉此完美避開 404 錯誤
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            
            # 優先順序：1.5 flash -> 1.5 pro -> 傳統 pro
            target_model = 'gemini-1.5-flash' # 預設值
            preferred = ['models/gemini-1.5-flash', 'models/gemini-1.5-pro', 'models/gemini-pro']
            for pm in preferred:
                if pm in available_models:
                    target_model = pm
                    break
            
            if target_model not in preferred and available_models:
                target_model = available_models[0] # 如果預設的都沒有，就隨便抓一個可用的
                
            model = genai.GenerativeModel(target_model)
            
            # 建構嚴格的系統提示詞
            system_instruction = f"""
你是一個由 HR 部門提供給員工的專業「公司規章問答機器人」。
請你「只」根據以下提供的【公司規章內容】來回答員工的問題。

【回答規則】：
1. 💡【排版鐵則：直接說重點且放大粗體】：請在回答的最開頭，直接針對員工的問題給出一個明確的「總結論」。這段總結論請務必使用 Markdown 的 `###` 語法（H3 標題）來讓字體放大加粗。例如：「### 根據規章，您目前可享有的特別休假日數為十日。」
2. 除了最開頭的這句總結論之外，接下來為了避免整個網頁字體過大難以閱讀，後續引用的規章與詳細說明段落，【絕對不要】使用任何 `#` 的大標題語法！請改用一般純文字、加粗字體 (**重點**) 或是數字列點 (1. 2. 3.) 排版即可。
3. 引用或列出條文參考時，請務必按照「先後順序（例如由第1條到第2條）」或「邏輯順序（由上到下）」往下排列，次序絕對不可以顛倒。
4. 若員工問題在規章中完全找不到答案，請回答：「抱歉，目前的規章中沒有明確規定這部分的細節，建議您直接向主管或人資單位（HR）確認。」絕對不可自己編造規章不存在的規定。

【公司規章內容開始】
{knowledge_base}
【公司規章內容結束】
"""
            
            # 組裝 Prompt
            full_prompt = system_instruction + "\n\n"
            
            # 加入最近的對話歷史 (避免 token 過長，保留前幾條對話幫助上下文理解)
            history_str = ""
            # 取最近的幾筆紀錄 (不包含最後一個也就是當前的 prompt)
            for msg in st.session_state.messages[-5:-1]: 
                role = "員工" if msg["role"] == "user" else "AI 助手"
                history_str += f"{role}：{msg['content']}\n"
                
            if history_str:
                full_prompt += "【最近的對話紀錄作為上下文參考】\n" + history_str + "\n"
            
            full_prompt += f"\n員工的問題是：{prompt}\n你的回答："
            
            # 呼叫 API (使用 Streaming 流式輸出讓體驗更好)
            response = model.generate_content(full_prompt, stream=True)
            
            full_response = ""
            for chunk in response:
                full_response += chunk.text
                message_placeholder.markdown(full_response + "▌")
            # 完整顯示
            message_placeholder.markdown(full_response)
            
            # 將機器人的回答加入紀錄
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
        except Exception as e:
            error_msg = f"發生連線錯誤，請檢查您的網路或確認 API Key 是否正確。詳細錯誤：{e}"
            message_placeholder.error(error_msg)
            # 將錯誤也記錄進對話歷史避免狀態不同步
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
