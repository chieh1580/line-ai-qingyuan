# ==========================================
# LINE AI 客服系統模板
# 使用說明：複製此資料夾，填入客戶資料後部署
# 需要填入的資料：
# 1. SYSTEM_PROMPT - 客戶品牌資訊和服務內容
# 2. Railway 環境變數：CLAUDE_API_KEY、LINE_TOKEN、ADMIN_PASSWORD、BRAND_NAME
# 3. BOSS_USER_ID - 老闆的 LINE User ID（用於推播通知）
# ==========================================

from flask import Flask, request, jsonify, render_template_string, make_response, redirect
import anthropic
import requests
import os
import json
from datetime import datetime
import sys
import threading


app = Flask(__name__)
app.logger.setLevel("INFO")
app.logger.addHandler(logging_handler := __import__('logging').StreamHandler(sys.stdout))
logging_handler.setLevel("INFO")

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
LINE_TOKEN = os.environ.get("LINE_TOKEN")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "請填入密碼")
BOSS_USER_ID = "U139541a789ae55afa3c3d551e966d6fb"
ADMIN_URL = os.environ.get("ADMIN_URL", "")

paused_users = set()
user_profiles = {}
app_logs = []
user_state = {}           # userId -> {"flow": "collecting_booking", "step": "name"}
user_booking_data = {}    # userId -> {"name": ..., "phone": ..., "time": ...}
user_message_count = {}   # userId -> int (追蹤互動次數，用於觸發見證卡片)
testimonial_sent = set()  # 已發送見證卡片的用戶
welcome_sent = set()      # 已發送歡迎卡片的用戶

TRIGGER_WORDS = ["找真人","找人工","找客服","找專員","真人","人工"]
BOOKING_KEYWORDS = ["我要預約", "我想預約", "預約看屋", "我要看房", "我想看房", "預約賞屋", "我想預約看屋"]

SYSTEM_PROMPT = """你是「小琪」，勤源青崧居的專業銷售顧問AI。語氣親切專業。

【物件資訊】
名稱：勤源青崧居
地址：桃園市龍潭區中興路187巷1弄35號

【房型與價格】
單套房：360萬，9.47坪，剩2戶，首購貸7成，自備約110萬，月負擔約15,000元
雙套房：660萬，18-20坪，剩4戶，首購貸7成，自備約185萬，軍職可申請優惠貸款

【地段優勢】
步行3分鐘：國軍桃園總醫院（804醫院）
步行5分鐘：中興路商圈（全聯、寶雅、郵局）
車程10分鐘：國道3號龍潭交流道

【社區設備】
全室黃山石木紋地磚、HCG和成衛浴、四合一暖風機
8人電梯、無人式飯店管理、專人倒垃圾
單套附1機車位、雙套附2機車位

【賞屋聯絡】
專線：0980-460395（林小姐）
預約制賞屋

【回覆原則】
1. 每次回覆結尾自然引導預約看屋
2. 終極目標：引導客人留下姓名和電話
3. 不確定的問題說「讓我幫您轉接專員確認，請問方便留下姓名和電話嗎？」
4. 回覆簡潔，引導客人繼續聊"""

SETTINGS_FILE = "/data/settings.json"


def _load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[SETTINGS] Save error: {e}", flush=True)
        return False


def get_setting(key, default=None):
    data = _load_settings()
    return data.get(key, default)


def set_setting(key, value):
    data = _load_settings()
    data[key] = value
    return _save_settings(data)

ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ brand_name }} 後台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#f7f5f2;color:#2d1f14}
.topbar{background:#f0ebe3;border-bottom:0.5px solid #e0d8ce;padding:16px 22px;display:flex;align-items:center;justify-content:space-between}
.topbar-brand{display:flex;align-items:center;gap:12px}
.topbar-logo{width:32px;height:32px;background:#c8401a;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff}
.topbar-name{font-size:15px;font-weight:600;color:#2d1f14}
.topbar-sub{font-size:11px;color:#b0a090;margin-top:2px}
.online{display:flex;align-items:center;gap:6px}
.pulse{width:7px;height:7px;border-radius:50%;background:#6abf69}
.online span{font-size:12px;color:#b0a090}
.tabs{display:flex;background:#f0ebe3;border-bottom:0.5px solid #e0d8ce;padding:0 20px}
.tab{padding:10px 18px;font-size:13px;font-weight:500;color:#b0a090;text-decoration:none;border-bottom:2px solid transparent}
.tab.active{color:#c8401a;border-bottom:2px solid #c8401a;font-weight:600}
.tab:hover{color:#2d1f14}
.stats{display:flex;gap:10px;padding:18px 20px 8px}
.stat{background:#fff;border-radius:10px;padding:14px 16px;flex:1;border:0.5px solid #e8e2d8}
.stat-n{font-size:26px;font-weight:600;color:#2d1f14}
.stat-n.orange{color:#c8401a}
.stat-n.green{color:#3b6d11}
.stat-l{font-size:11px;color:#b0a090;margin-top:2px}
.notify{margin:8px 20px 4px;background:#fff8f4;border:0.5px solid #f0c8b0;border-radius:8px;padding:11px 14px;display:flex;align-items:center;gap:10px}
.notify-dot{width:7px;height:7px;border-radius:50%;background:#c8401a;flex-shrink:0}
.notify-txt{font-size:12px;color:#8b3a1a}
.main{padding:14px 20px 24px}
.sec-label{font-size:11px;font-weight:600;color:#c8b8a8;letter-spacing:2px;margin-bottom:10px;margin-top:4px}
.card{background:#fff;border-radius:10px;padding:13px 15px;margin-bottom:8px;border:0.5px solid #e8e2d8;display:flex;align-items:center;gap:12px}
.card.paused{border-left:3px solid #c8401a;border-radius:0 10px 10px 0;background:#fffaf7}
.card.active{border-left:3px solid #6abf69;border-radius:0 10px 10px 0}
.ava{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;flex-shrink:0;background:#f0ebe3;color:#8b3a1a;overflow:hidden}
.ava img{width:100%;height:100%;object-fit:cover}
.uinfo{flex:1;min-width:0}
.uname{font-size:13px;font-weight:600;color:#2d1f14}
.umsg{font-size:12px;color:#b0a090;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}
.utime{font-size:11px;color:#ccc;margin-top:2px}
.badge{font-size:11px;padding:3px 9px;border-radius:10px;font-weight:500;flex-shrink:0}
.badge-ai{background:#d4e8d0;color:#27500a}
.badge-human{background:#f5d5c8;color:#712b13}
.btn{border:0.5px solid;border-radius:6px;padding:6px 12px;font-size:12px;font-weight:500;cursor:pointer;flex-shrink:0;transition:0.15s}
.btn-stop{background:#fff0ec;color:#712b13;border-color:#e8c0b0}
.btn-stop:hover{background:#f5d5c8}
.btn-go{background:#d4e8d0;color:#27500a;border-color:#b0d0a8}
.btn-go:hover{background:#c0ddb8}
.divider{height:0.5px;background:#e8e2d8;margin:14px 0}
.empty{text-align:center;padding:40px;color:#c8b8a8;font-size:14px}
.login-wrap{display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f7f5f2}
.login-box{background:#fff;border-radius:12px;padding:32px;width:300px;border:0.5px solid #e8e2d8;text-align:center}
.login-logo{width:48px;height:48px;background:#c8401a;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;color:#fff;margin:0 auto 16px}
.login-box h2{font-size:16px;font-weight:600;margin-bottom:20px;color:#2d1f14}
.login-box input{width:100%;padding:10px 14px;border:0.5px solid #e0d8ce;border-radius:6px;font-size:14px;margin-bottom:12px;text-align:center;background:#f7f5f2}
.login-box button{width:100%;padding:10px;background:#c8401a;color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer}
.err{color:#c8401a;font-size:12px;margin-top:8px}
.toast{position:fixed;bottom:20px;right:20px;background:#2d1f14;color:#f5ede0;padding:10px 18px;border-radius:6px;font-size:13px;display:none;z-index:999}
</style>
</head>
<body>
{% if not authenticated %}
<div class="login-wrap">
  <div class="login-box">
    <div class="login-logo">AI</div>
    <h2>後台管理登入</h2>
    <form method="POST" action="/admin/login">
      <input type="password" name="password" placeholder="請輸入密碼" required>
      <button type="submit">登入</button>
    </form>
    {% if error %}<p class="err">密碼錯誤，請再試一次</p>{% endif %}
  </div>
</div>
{% else %}
<div class="topbar">
  <div class="topbar-brand">
    <div class="topbar-logo">AI</div>
    <div>
      <div class="topbar-name">{{ brand_name }} 後台</div>
      <div class="topbar-sub">LINE AI 客服管理系統</div>
    </div>
  </div>
  <div class="online">
    <div class="pulse"></div>
    <span>系統運作中</span>
  </div>
</div>
<div class="tabs">
  <a href="/admin" class="tab active">對話管理</a>
  <a href="/admin/settings" class="tab">設定</a>
</div>

<div class="stats">
  <div class="stat"><div class="stat-n">{{ total }}</div><div class="stat-l">今日對話</div></div>
  <div class="stat"><div class="stat-n green">{{ active }}</div><div class="stat-l">AI 回覆中</div></div>
  <div class="stat"><div class="stat-n orange">{{ paused_count }}</div><div class="stat-l">待人工處理</div></div>
  <div class="stat"><div class="stat-n">{{ ai_rate }}<span style="font-size:13px;color:#bbb;">%</span></div><div class="stat-l">AI 回覆率</div></div>
</div>

{% if pending_users %}
<div class="notify">
  <div class="notify-dot"></div>
  <div class="notify-txt">{{ pending_users[0].name }} 需要您回覆，共 {{ paused_count }} 位客人等待中</div>
</div>
{% endif %}

<div class="main">
  {% if paused_users_list %}
  <div class="sec-label">待處理</div>
  {% for u in paused_users_list %}
  <div class="card paused">
    <div class="ava">
      {% if u.picture %}<img src="{{ u.picture }}" onerror="this.style.display='none'">{% else %}{{ u.name[0] }}{% endif %}
    </div>
    <div class="uinfo">
      <div class="uname">{{ u.name }}</div>
      <div class="umsg">{{ u.lastMessage }}</div>
      <div class="utime">{{ u.lastTime }}</div>
    </div>
    <span class="badge badge-human">人工中</span>
    <button class="btn btn-go" onclick="toggle('{{ u.id }}','resume')">恢復 AI</button>
  </div>
  {% endfor %}
  <div class="divider"></div>
  {% endif %}

  {% if active_users %}
  <div class="sec-label">AI 回覆中</div>
  {% for u in active_users %}
  <div class="card active">
    <div class="ava">
      {% if u.picture %}<img src="{{ u.picture }}" onerror="this.style.display='none'">{% else %}{{ u.name[0] }}{% endif %}
    </div>
    <div class="uinfo">
      <div class="uname">{{ u.name }}</div>
      <div class="umsg">{{ u.lastMessage }}</div>
      <div class="utime">{{ u.lastTime }}</div>
    </div>
    <span class="badge badge-ai">AI 中</span>
    <button class="btn btn-stop" onclick="toggle('{{ u.id }}','pause')">暫停 AI</button>
  </div>
  {% endfor %}
  {% endif %}

  {% if not paused_users_list and not active_users %}
  <div class="empty">還沒有客人傳訊息進來</div>
  {% endif %}
</div>

<div class="toast" id="toast"></div>
<script>
function toggle(uid, action) {
  fetch('/admin/toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({userId: uid, action: action})
  }).then(r => r.json()).then(() => {
    const t = document.getElementById('toast')
    t.textContent = action === 'pause' ? '已暫停 AI，換您回覆' : '已恢復 AI 自動回覆'
    t.style.display = 'block'
    setTimeout(() => { t.style.display = 'none'; location.reload() }, 1000)
  })
}
setTimeout(() => location.reload(), 30000)
</script>
{% endif %}
</body>
</html>"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ brand_name }} 設定</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#f7f5f2;color:#2d1f14}
.topbar{background:#f0ebe3;border-bottom:0.5px solid #e0d8ce;padding:16px 22px;display:flex;align-items:center;justify-content:space-between}
.topbar-brand{display:flex;align-items:center;gap:12px}
.topbar-logo{width:32px;height:32px;background:#c8401a;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff}
.topbar-name{font-size:15px;font-weight:600;color:#2d1f14}
.topbar-sub{font-size:11px;color:#b0a090;margin-top:2px}
.tabs{display:flex;background:#f0ebe3;border-bottom:0.5px solid #e0d8ce;padding:0 20px}
.tab{padding:10px 18px;font-size:13px;font-weight:500;color:#b0a090;text-decoration:none;border-bottom:2px solid transparent}
.tab.active{color:#c8401a;border-bottom:2px solid #c8401a;font-weight:600}
.tab:hover{color:#2d1f14}
.main{padding:18px 20px 24px;max-width:700px}
.card{background:#fff;border-radius:10px;padding:18px;margin-bottom:14px;border:0.5px solid #e8e2d8}
.card-title{font-size:13px;font-weight:600;color:#2d1f14;margin-bottom:8px}
.card-desc{font-size:11px;color:#b0a090;margin-bottom:10px}
textarea{width:100%;border:0.5px solid #e0d8ce;border-radius:6px;padding:10px 12px;font-size:13px;font-family:-apple-system,sans-serif;background:#f7f5f2;color:#2d1f14;resize:vertical;line-height:1.6}
textarea:focus{outline:none;border-color:#c8401a}
.btn-row{display:flex;gap:10px;margin-top:16px}
.btn-save{background:#c8401a;color:#fff;border:none;border-radius:6px;padding:10px 24px;font-size:13px;font-weight:600;cursor:pointer}
.btn-save:hover{background:#a83515}
.btn-reset{background:#fff;color:#8b3a1a;border:0.5px solid #e8c0b0;border-radius:6px;padding:10px 24px;font-size:13px;font-weight:500;cursor:pointer}
.btn-reset:hover{background:#fff8f4}
.toast{position:fixed;bottom:20px;right:20px;background:#2d1f14;color:#f5ede0;padding:10px 18px;border-radius:6px;font-size:13px;display:none;z-index:999}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-brand">
    <div class="topbar-logo">AI</div>
    <div>
      <div class="topbar-name">{{ brand_name }} 後台</div>
      <div class="topbar-sub">LINE AI 客服管理系統</div>
    </div>
  </div>
</div>
<div class="tabs">
  <a href="/admin" class="tab">對話管理</a>
  <a href="/admin/settings" class="tab active">設定</a>
</div>
<div class="main">
  <div class="card">
    <div class="card-title">機器人指令 (System Prompt)</div>
    <div class="card-desc">控制機器人的角色、口氣、回覆風格和所有資訊內容（電話、地址、價格等）</div>
    <textarea id="prompt" rows="16">{{ system_prompt }}</textarea>
  </div>
  <div class="card">
    <div class="card-title">轉人工關鍵字</div>
    <div class="card-desc">當客人訊息包含以下任一關鍵字時，自動暫停 AI 並通知您（一行一個）</div>
    <textarea id="triggers" rows="6">{{ trigger_words }}</textarea>
  </div>
  <div class="btn-row">
    <button class="btn-save" onclick="saveSettings()">儲存設定</button>
    <button class="btn-reset" onclick="resetSettings()">恢復預設</button>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 2000);
}
function saveSettings() {
  const prompt = document.getElementById('prompt').value.trim();
  const triggers = document.getElementById('triggers').value.trim();
  if (!prompt) { showToast('Prompt 不能為空'); return; }
  fetch('/admin/settings/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({system_prompt: prompt, trigger_words: triggers})
  }).then(r => r.json()).then(d => {
    showToast(d.status === 'ok' ? '設定已儲存，立即生效！' : '儲存失敗：' + (d.error || '未知錯誤'));
  }).catch(() => showToast('儲存失敗，請重試'));
}
function resetSettings() {
  if (!confirm('確定要恢復為預設設定嗎？')) return;
  fetch('/admin/settings/reset', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'}
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') { showToast('已恢復預設'); setTimeout(() => location.reload(), 1000); }
    else showToast('操作失敗');
  }).catch(() => showToast('操作失敗，請重試'));
}
</script>
</body>
</html>"""


# ===== LINE API 通用函式 =====
def reply_messages(reply_token, messages):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
        json={"replyToken": reply_token, "messages": messages},
        timeout=10
    )


def push_messages(user_id, messages):
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
        json={"to": user_id, "messages": messages},
        timeout=10
    )
    log_msg = f"[PUSH] to={user_id[-6:]} status={r.status_code}"
    print(log_msg, flush=True)
    app_logs.append({"time": datetime.now().strftime("%m/%d %H:%M:%S"), "msg": log_msg})
    return r


def push_text(user_id, text):
    push_messages(user_id, [{"type": "text", "text": text}])


def push_flex(user_id, flex):
    push_messages(user_id, [flex])


# ===== Flex Message 建構 =====
def build_welcome_flex():
    """Follow 歡迎卡片 — 房地產版"""
    return {
        "type": "flex",
        "altText": "歡迎加入勤源青崧居！",
        "contents": {
            "type": "bubble",
            "size": "giga",
            "header": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "🏠 歡迎來到勤源青崧居", "weight": "bold", "size": "lg", "color": "#1a5c2e"},
                    {"type": "text", "text": "龍潭收租金雞母，醫護剛需首選 💰", "size": "md", "margin": "sm", "color": "#555555"}
                ],
                "paddingAll": "20px", "backgroundColor": "#f0f7f2"
            },
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "我是您的專屬賞屋顧問「小琪」🙋‍♀️\n有任何問題都可以問我，也可以直接點選下方按鈕快速了解！", "wrap": True, "size": "sm", "color": "#666666"},
                    {"type": "separator", "margin": "lg"},
                    {"type": "text", "text": "👇 您想了解什麼呢？", "weight": "bold", "size": "md", "margin": "lg", "color": "#2d1f14"},
                    {
                        "type": "box", "layout": "horizontal", "spacing": "sm", "margin": "md",
                        "contents": [
                            {"type": "button", "action": {"type": "message", "label": "🏠 房型與價格", "text": "你們有哪些房型？價格怎麼算？"}, "style": "primary", "color": "#1a5c2e", "height": "sm"},
                            {"type": "button", "action": {"type": "message", "label": "📍 地段優勢", "text": "這個建案的地段有什麼優勢？"}, "style": "primary", "color": "#2e7d4a", "height": "sm"}
                        ]
                    },
                    {
                        "type": "box", "layout": "horizontal", "spacing": "sm", "margin": "sm",
                        "contents": [
                            {"type": "button", "action": {"type": "message", "label": "💰 投資報酬", "text": "投資報酬率大概多少？適合投資嗎？"}, "style": "primary", "color": "#3d8b5e", "height": "sm"},
                            {"type": "button", "action": {"type": "message", "label": "📅 預約看屋", "text": "我想預約看屋"}, "style": "primary", "color": "#c8401a", "height": "sm"}
                        ]
                    },
                    {"type": "separator", "margin": "lg"},
                    {"type": "text", "text": "💡 也可以直接打字問我任何問題哦！", "wrap": True, "size": "xs", "color": "#999999", "margin": "lg"}
                ],
                "paddingAll": "20px"
            }
        }
    }


def build_booking_start_flex():
    """預約看屋 — 第一步：詢問姓名"""
    return {
        "type": "flex",
        "altText": "太好了！幫您安排預約看屋",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "太好了！🎉", "weight": "bold", "size": "lg", "color": "#1a5c2e"},
                    {"type": "text", "text": "讓我幫您安排賞屋，只需要簡單 3 個資訊：", "wrap": True, "size": "sm", "color": "#666666", "margin": "md"},
                    {"type": "separator", "margin": "lg"},
                    {"type": "text", "text": "① 請問您怎麼稱呼？", "weight": "bold", "size": "md", "margin": "lg", "color": "#2d1f14"},
                    {"type": "text", "text": "直接打字回覆就好囉 ✏️", "size": "xs", "color": "#999999", "margin": "sm"}
                ],
                "paddingAll": "20px"
            }
        }
    }


def build_booking_complete_flex(data):
    """預約看屋 — 完成確認卡片"""
    return {
        "type": "flex",
        "altText": "預約資料收到囉！",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "預約資料收到囉！✅", "weight": "bold", "size": "lg", "color": "#1a5c2e"},
                    {"type": "separator", "margin": "lg"},
                    {"type": "box", "layout": "horizontal", "margin": "lg", "contents": [
                        {"type": "text", "text": "姓名", "size": "sm", "color": "#999999", "flex": 2},
                        {"type": "text", "text": data.get("name", ""), "size": "sm", "weight": "bold", "flex": 4}
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "電話", "size": "sm", "color": "#999999", "flex": 2},
                        {"type": "text", "text": data.get("phone", ""), "size": "sm", "weight": "bold", "flex": 4}
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "方便時間", "size": "sm", "color": "#999999", "flex": 2},
                        {"type": "text", "text": data.get("time", ""), "size": "sm", "weight": "bold", "flex": 4}
                    ]},
                    {"type": "separator", "margin": "lg"},
                    {"type": "text", "text": "林小姐會盡快透過電話或 LINE 與您確認賞屋時間，請留意來電 📱", "wrap": True, "size": "sm", "color": "#666666", "margin": "lg"}
                ],
                "paddingAll": "20px"
            }
        }
    }


def build_notify_boss_flex(customer_name, name, phone, preferred_time, time_str):
    """通知老闆 Flex 卡片 — 新的預約看屋"""
    return {
        "type": "flex",
        "altText": f"🔔 新的預約看屋：{name}",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "text", "text": "🔔 新的預約看屋！", "weight": "bold", "size": "lg", "color": "#c8401a"}],
                "paddingAll": "16px", "backgroundColor": "#FFF8F4"
            },
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "LINE 名稱", "size": "sm", "color": "#999999", "flex": 3},
                        {"type": "text", "text": customer_name, "size": "sm", "weight": "bold", "flex": 5}
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "姓名", "size": "sm", "color": "#999999", "flex": 3},
                        {"type": "text", "text": name or "未提供", "size": "sm", "weight": "bold", "flex": 5}
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "電話", "size": "sm", "color": "#999999", "flex": 3},
                        {"type": "text", "text": phone or "未提供", "size": "sm", "weight": "bold", "flex": 5}
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "方便時間", "size": "sm", "color": "#999999", "flex": 3},
                        {"type": "text", "text": preferred_time or "未提供", "size": "sm", "weight": "bold", "flex": 5}
                    ]},
                    {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                        {"type": "text", "text": "提交時間", "size": "sm", "color": "#999999", "flex": 3},
                        {"type": "text", "text": time_str, "size": "sm", "flex": 5}
                    ]},
                    {"type": "separator", "margin": "lg"},
                    {"type": "button", "action": {"type": "uri", "label": "👉 查看後台", "uri": ADMIN_URL or "https://line-ai-qingyuan-production.up.railway.app/admin"}, "style": "primary", "color": "#c8401a", "margin": "lg", "height": "sm"}
                ],
                "paddingAll": "16px"
            }
        }
    }


def build_testimonial_flex():
    """見證卡片 — 買家/租客心得"""
    return {
        "type": "flex",
        "altText": "🏆 看看其他屋主怎麼說",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "text", "text": "🏆 屋主真心分享", "weight": "bold", "size": "md", "color": "#2d1f14"}],
                "paddingAll": "16px", "backgroundColor": "#f0f7f2"
            },
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "「當初看中離 804 醫院走路 3 分鐘，買來收租，第一個月就租出去了，租客是醫院護理師，穩定又安心！」", "wrap": True, "size": "sm", "color": "#555555", "style": "italic"},
                    {"type": "text", "text": "— 單套房屋主 陳先生", "size": "xs", "color": "#999999", "margin": "md", "align": "end"},
                    {"type": "separator", "margin": "lg"},
                    {
                        "type": "box", "layout": "horizontal", "margin": "lg",
                        "contents": [
                            {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                                {"type": "text", "text": "年報酬率", "size": "xs", "color": "#999999", "align": "center"},
                                {"type": "text", "text": "3%↑", "size": "xl", "weight": "bold", "color": "#c8401a", "align": "center"}
                            ]},
                            {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                                {"type": "text", "text": "出租速度", "size": "xs", "color": "#999999", "align": "center"},
                                {"type": "text", "text": "1個月", "size": "xl", "weight": "bold", "color": "#1a5c2e", "align": "center"}
                            ]},
                            {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                                {"type": "text", "text": "剩餘戶數", "size": "xs", "color": "#999999", "align": "center"},
                                {"type": "text", "text": "倒數6戶", "size": "xl", "weight": "bold", "color": "#1a6bc8", "align": "center"}
                            ]}
                        ]
                    },
                    {"type": "separator", "margin": "lg"},
                    {"type": "button", "action": {"type": "message", "label": "我也想了解 👋", "text": "我想預約看屋"}, "style": "primary", "color": "#1a5c2e", "margin": "lg", "height": "sm"}
                ],
                "paddingAll": "16px"
            }
        }
    }


# ===== 延遲推播跟進 =====
def schedule_followups(user_id):
    """加好友後排程 24hr / 48hr / 7天 自動跟進"""
    followup_configs = [
        (86400, "24hr"),
        (172800, "48hr"),
        (604800, "7day"),
    ]
    for delay, msg_type in followup_configs:
        timer = threading.Timer(delay, send_followup, args=[user_id, msg_type])
        timer.daemon = True
        timer.start()


def send_followup(user_id, msg_type):
    # 如果已經預約過，不再跟進
    if user_id in user_booking_data:
        return

    messages = {
        "24hr": {
            "type": "flex", "altText": "還沒來得及了解嗎？",
            "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
                {"type": "text", "text": "嗨～還沒來得及了解嗎？👋", "weight": "bold", "size": "md", "color": "#2d1f14"},
                {"type": "text", "text": "青崧居單套房 360 萬起，離 804 醫院走路 3 分鐘，買來收租超划算！\n\n試著問我任何問題，3 秒就有答案 😊", "wrap": True, "size": "sm", "color": "#666666", "margin": "md"},
                {"type": "button", "action": {"type": "message", "label": "看看房型與價格 💬", "text": "你們有哪些房型？價格怎麼算？"}, "style": "primary", "color": "#1a5c2e", "margin": "lg", "height": "sm"}
            ]}}
        },
        "48hr": {
            "type": "flex", "altText": "很多人最好奇的問題",
            "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
                {"type": "text", "text": "很多人最好奇的是… 🤔", "weight": "bold", "size": "md", "color": "#2d1f14"},
                {"type": "text", "text": "「自備款要多少？」\n「月繳負擔重嗎？」", "wrap": True, "size": "sm", "color": "#666666", "margin": "md"},
                {"type": "text", "text": "單套房首購自備約 110 萬，月繳約 15,000 元，比繳房租還划算！而且租金收入還能 cover 一大半 💪", "wrap": True, "size": "sm", "color": "#555555", "margin": "md"},
                {"type": "button", "action": {"type": "message", "label": "我想了解更多", "text": "投資報酬率大概多少？適合投資嗎？"}, "style": "primary", "color": "#1a5c2e", "margin": "lg", "height": "sm"}
            ]}}
        },
        "7day": {
            "type": "flex", "altText": "倒數戶數提醒",
            "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
                {"type": "text", "text": "🏠 戶數倒數中！", "weight": "bold", "size": "md", "color": "#c8401a"},
                {"type": "text", "text": "青崧居剩餘戶數不多囉！\n單套房剩 2 戶、雙套房剩 4 戶\n\n有興趣的話趕快來看看，錯過就沒了 🔥", "wrap": True, "size": "sm", "color": "#666666", "margin": "md"},
                {"type": "button", "action": {"type": "message", "label": "立即預約看屋 📅", "text": "我想預約看屋"}, "style": "primary", "color": "#c8401a", "margin": "lg", "height": "sm"}
            ]}}
        }
    }

    msg = messages.get(msg_type)
    if msg:
        push_flex(user_id, msg)
        log_msg = f"[FOLLOWUP] {msg_type} sent to {user_id[-6:]}"
        print(log_msg, flush=True)
        app_logs.append({"time": datetime.now().strftime("%m/%d %H:%M:%S"), "msg": log_msg})


# ===== 按鈕引導卡片 =====
GUIDED_BUTTONS = {
    "你們有哪些房型？價格怎麼算？": {
        "title": "🏠 房型與價格",
        "info": [
            "【單套房】360 萬｜9.47 坪",
            "　一房一衛一陽台，剩 2 戶",
            "　首購貸 7 成，自備約 110 萬",
            "　月繳約 15,000 元",
            "",
            "【雙套房】660 萬｜18～20 坪",
            "　兩房兩廳兩衛浴兩陽台，剩 4 戶",
            "　首購貸 7 成，自備約 185 萬",
            "　軍職可申請優惠貸款",
            "",
            "✨ 全部含精美裝潢、家具、變頻冷暖空調",
        ]
    },
    "這個建案的地段有什麼優勢？": {
        "title": "📍 地段優勢",
        "info": [
            "🏥 步行 3 分鐘 → 國軍桃園總醫院（804）",
            "　醫護、軍官租屋剛性需求",
            "",
            "🛒 步行 5 分鐘 → 中興路商圈",
            "　全聯、寶雅、錢都、郵局、超商",
            "",
            "🚗 車程 10 分鐘 → 國道 3 號龍潭交流道",
            "　南往竹科、北往三峽",
            "",
            "📌 地址：桃園市龍潭區中興路187巷1弄35號",
        ]
    },
    "投資報酬率大概多少？適合投資嗎？": {
        "title": "💰 投資亮點",
        "info": [
            "📈 年報酬率 3% 起",
            "🛡️ 抗通膨保值投資",
            "🔑 即買即收租，買完馬上出租",
            "👨‍⚕️ 租客以醫護、軍官為主，品質穩定",
            "🏢 代租代管諮詢，輕鬆當房東",
            "",
            "💡 單套房月租約 9,000～10,000 元",
            "　月繳房貸約 15,000 元",
            "　租金 cover 大部分，等於租客幫你繳房貸！",
        ]
    },
}


def build_guided_flex(user_message):
    """按鈕引導卡片 — 結構化資訊 + 下一步按鈕"""
    config = GUIDED_BUTTONS[user_message]
    info_text = "\n".join(config["info"])

    # 其他兩個按鈕（排除當前已選的）
    other_buttons = []
    button_map = {
        "你們有哪些房型？價格怎麼算？": ("🏠 房型與價格", "#1a5c2e"),
        "這個建案的地段有什麼優勢？": ("📍 地段優勢", "#2e7d4a"),
        "投資報酬率大概多少？適合投資嗎？": ("💰 投資報酬", "#3d8b5e"),
    }
    for text, (label, color) in button_map.items():
        if text != user_message:
            other_buttons.append(
                {"type": "button", "action": {"type": "message", "label": label, "text": text}, "style": "secondary", "height": "sm"}
            )

    return {
        "type": "flex",
        "altText": config["title"],
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "text", "text": config["title"], "weight": "bold", "size": "lg", "color": "#1a5c2e"}],
                "paddingAll": "16px", "backgroundColor": "#f0f7f2"
            },
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": info_text, "wrap": True, "size": "sm", "color": "#555555"},
                    {"type": "separator", "margin": "lg"},
                    {"type": "text", "text": "👇 繼續了解", "weight": "bold", "size": "sm", "margin": "lg", "color": "#2d1f14"},
                    {"type": "box", "layout": "vertical", "margin": "md", "spacing": "sm", "contents": other_buttons + [
                        {"type": "button", "action": {"type": "message", "label": "📅 預約看屋", "text": "我想預約看屋"}, "style": "primary", "color": "#c8401a", "height": "sm"}
                    ]}
                ],
                "paddingAll": "16px"
            }
        }
    }


def notify_boss_booking(customer_name, name, phone, preferred_time):
    """通知老闆：新的預約看屋"""
    time_str = datetime.now().strftime("%m/%d %H:%M")
    flex = build_notify_boss_flex(customer_name, name, phone, preferred_time, time_str)
    push_flex(BOSS_USER_ID, flex)


def get_line_profile(user_id):
    try:
        r = requests.get(
            f"https://api.line.me/v2/bot/profile/{user_id}",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return {"displayName": "用戶" + user_id[-4:], "pictureUrl": ""}


def reply_to_user(reply_token, message):
    reply_messages(reply_token, [{"type": "text", "text": message}])


def notify_boss(customer_name, message, time_str):
    text = (
        f"\U0001f514 \u6709\u5ba2\u4eba\u9700\u8981\u60a8\u56de\u8986\uff01\n"
        f"\u5ba2\u4eba\uff1a{customer_name}\n"
        f"\u8a0a\u606f\uff1a{message}\n"
        f"\u6642\u9593\uff1a{time_str}\n"
        f"\U0001f449 \u5f8c\u53f0\uff1a{ADMIN_URL}"
    )
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
        json={"to": BOSS_USER_ID, "messages": [{"type": "text", "text": text}]},
        timeout=10
    )
    log_msg = f"[NOTIFY_BOSS] status={r.status_code} response={r.text}"
    print(log_msg, flush=True)
    app_logs.append({"time": datetime.now().strftime("%m/%d %H:%M:%S"), "msg": log_msg})


def ask_claude(user_message):
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=get_setting('system_prompt', SYSTEM_PROMPT),
        messages=[{"role": "user", "content": user_message}]
    )
    return msg.content[0].text


@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body:
        return jsonify({"status": "ok"})

    for event in body["events"]:
        event_type = event.get("type")

        # ===== Follow Event：加好友歡迎卡片 =====
        if event_type == "follow":
            user_id = event["source"]["userId"]
            reply_token = event["replyToken"]
            log_msg = f"[FOLLOW] new follower: {user_id[-6:]}"
            print(log_msg, flush=True)
            app_logs.append({"time": datetime.now().strftime("%m/%d %H:%M:%S"), "msg": log_msg})

            profile = get_line_profile(user_id)
            user_profiles[user_id] = {
                "name": profile.get("displayName", "用戶"),
                "picture": profile.get("pictureUrl", ""),
                "lastMessage": "（剛加好友）",
                "lastTime": datetime.now().strftime("%m/%d %H:%M")
            }

            welcome_sent.add(user_id)
            reply_messages(reply_token, [build_welcome_flex()])
            schedule_followups(user_id)
            continue

        # ===== 只處理文字訊息 =====
        if event_type != "message":
            continue
        if event["message"].get("type") != "text":
            continue

        user_id = event["source"]["userId"]
        reply_token = event["replyToken"]
        user_message = event["message"]["text"].strip()
        log_msg = f"[MSG] {user_id[-6:]}: {user_message[:50]}"
        print(log_msg, flush=True)
        app_logs.append({"time": datetime.now().strftime("%m/%d %H:%M:%S"), "msg": log_msg})

        # 更新用戶資料
        if user_id not in user_profiles:
            profile = get_line_profile(user_id)
            user_profiles[user_id] = {
                "name": profile.get("displayName", "用戶"),
                "picture": profile.get("pictureUrl", ""),
                "lastMessage": user_message,
                "lastTime": datetime.now().strftime("%m/%d %H:%M")
            }
        else:
            user_profiles[user_id]["lastMessage"] = user_message
            user_profiles[user_id]["lastTime"] = datetime.now().strftime("%m/%d %H:%M")

        # ----- 0. 舊用戶第一次互動，補發歡迎卡片 -----
        if user_id not in welcome_sent:
            welcome_sent.add(user_id)
            reply_messages(reply_token, [build_welcome_flex()])
            continue

        # ----- 1. 檢查：是否在預約看屋資料收集流程中 -----
        if user_id in user_state and user_state[user_id].get("flow") == "collecting_booking":
            step = user_state[user_id].get("step")

            if step == "name":
                user_booking_data.setdefault(user_id, {})
                user_booking_data[user_id]["name"] = user_message
                user_state[user_id]["step"] = "phone"
                reply_messages(reply_token, [
                    {"type": "flex", "altText": "請留下電話",
                     "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
                         {"type": "text", "text": f"收到！{user_message} 您好 👍", "weight": "bold", "size": "md", "color": "#2d1f14"},
                         {"type": "text", "text": "② 請留下您的聯絡電話", "weight": "bold", "size": "md", "margin": "lg", "color": "#2d1f14"},
                         {"type": "text", "text": "方便林小姐跟您確認賞屋時間 📞", "size": "xs", "color": "#999999", "margin": "sm"}
                     ]}}}
                ])
                continue

            elif step == "phone":
                user_booking_data.setdefault(user_id, {})
                user_booking_data[user_id]["phone"] = user_message
                user_state[user_id]["step"] = "time"
                reply_messages(reply_token, [
                    {"type": "flex", "altText": "請選擇方便時間",
                     "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
                         {"type": "text", "text": "③ 最後一題！您方便什麼時間來看屋呢？", "weight": "bold", "size": "md", "color": "#2d1f14"},
                         {"type": "box", "layout": "vertical", "margin": "md", "spacing": "sm", "contents": [
                             {"type": "button", "action": {"type": "message", "label": "平日白天（週一～五）", "text": "平日白天"}, "style": "secondary", "height": "sm"},
                             {"type": "button", "action": {"type": "message", "label": "平日晚上（週一～五）", "text": "平日晚上"}, "style": "secondary", "height": "sm"},
                             {"type": "button", "action": {"type": "message", "label": "週末（六日）", "text": "週末"}, "style": "secondary", "height": "sm"},
                             {"type": "button", "action": {"type": "message", "label": "都可以，配合安排", "text": "都可以"}, "style": "secondary", "height": "sm"}
                         ]}
                     ]}}}
                ])
                continue

            elif step == "time":
                user_booking_data.setdefault(user_id, {})
                user_booking_data[user_id]["time"] = user_message
                del user_state[user_id]
                customer_name = user_profiles.get(user_id, {}).get("name", "用戶")
                data = user_booking_data[user_id]

                # 通知老闆
                notify_boss_booking(
                    customer_name,
                    data.get("name", ""),
                    data.get("phone", ""),
                    data.get("time", "")
                )

                reply_messages(reply_token, [build_booking_complete_flex(data)])
                continue

        # ----- 2. 檢查：按鈕引導（房型/地段/投資） -----
        if user_message in GUIDED_BUTTONS:
            reply_messages(reply_token, [build_guided_flex(user_message)])
            continue

        # ----- 3. 檢查：預約看屋關鍵字 -----
        if any(kw in user_message for kw in BOOKING_KEYWORDS):
            user_state[user_id] = {"flow": "collecting_booking", "step": "name"}
            user_booking_data[user_id] = {}
            reply_messages(reply_token, [build_booking_start_flex()])
            continue

        # ----- 4. 檢查：暫停中的用戶 -----
        if user_id in paused_users:
            continue

        # ----- 5. 檢查：找真人（暫停 AI） -----
        current_triggers = json.loads(get_setting('trigger_words', json.dumps(TRIGGER_WORDS)))
        if any(word in user_message for word in current_triggers):
            paused_users.add(user_id)
            reply_to_user(reply_token, "好的！我馬上幫您通知專人，請稍候片刻，我們會盡快與您聯繫 🙏")
            customer_name = user_profiles[user_id]["name"]
            time_str = user_profiles[user_id]["lastTime"]
            notify_boss(customer_name, user_message, time_str)
            continue

        # ----- 6. AI 回覆 + 見證卡片觸發 -----
        try:
            ai_response = ask_claude(user_message)
            reply_to_user(reply_token, ai_response)

            # 追蹤互動次數，第 3 次後推送見證卡片
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            if user_message_count[user_id] == 3 and user_id not in testimonial_sent:
                testimonial_sent.add(user_id)
                timer = threading.Timer(3.0, push_flex, args=[user_id, build_testimonial_flex()])
                timer.daemon = True
                timer.start()

        except Exception as e:
            log_msg = f"[ERROR] Claude API: {str(e)}"
            print(log_msg, flush=True)
            app_logs.append({"time": datetime.now().strftime("%m/%d %H:%M:%S"), "msg": log_msg})
            reply_to_user(reply_token, "抱歉，系統暫時忙碌中，請稍後再試或直接聯繫我們 🙏")

    return jsonify({"status": "ok"})


@app.route("/admin")
def admin():
    authenticated = request.cookies.get("admin_auth") == ADMIN_PASSWORD
    brand_name = "\u52e4\u6e90\u9752\u5d27\u5c45"

    all_users = []
    for uid, p in user_profiles.items():
        all_users.append({
            "id": uid,
            "name": p["name"],
            "picture": p.get("picture", ""),
            "lastMessage": p.get("lastMessage", ""),
            "lastTime": p.get("lastTime", ""),
            "paused": uid in paused_users
        })
    all_users.sort(key=lambda x: x["lastTime"], reverse=True)

    paused_list = [u for u in all_users if u["paused"]]
    active_list = [u for u in all_users if not u["paused"]]
    total = len(all_users)
    paused_count = len(paused_list)
    active_count = len(active_list)
    ai_rate = round((active_count / total * 100) if total > 0 else 100)

    html = render_template_string(
        ADMIN_HTML,
        authenticated=authenticated,
        brand_name=brand_name,
        paused_users_list=paused_list,
        active_users=active_list,
        pending_users=paused_list,
        total=total,
        active=active_count,
        paused_count=paused_count,
        ai_rate=ai_rate,
        error=False
    )
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@app.route("/admin/login", methods=["POST"])
def admin_login():
    password = request.form.get("password")
    if password == ADMIN_PASSWORD:
        resp = make_response(redirect("/admin"))
        resp.set_cookie("admin_auth", ADMIN_PASSWORD, max_age=86400 * 7)
        return resp
    brand_name = "\u52e4\u6e90\u9752\u5d27\u5c45"
    html = render_template_string(
        ADMIN_HTML, authenticated=False, brand_name=brand_name,
        paused_users_list=[], active_users=[], pending_users=[],
        total=0, active=0, paused_count=0, ai_rate=100, error=True
    )
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@app.route("/admin/settings")
def admin_settings():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return redirect("/admin")
    brand_name = "\u52e4\u6e90\u9752\u5d27\u5c45"
    current_prompt = get_setting('system_prompt', SYSTEM_PROMPT)
    current_triggers = json.loads(get_setting('trigger_words', json.dumps(TRIGGER_WORDS)))
    trigger_text = "\n".join(current_triggers)
    html = render_template_string(
        SETTINGS_HTML,
        brand_name=brand_name,
        system_prompt=current_prompt,
        trigger_words=trigger_text
    )
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@app.route("/admin/settings/save", methods=["POST"])
def admin_settings_save():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    prompt = data.get("system_prompt", "").strip()
    triggers_text = data.get("trigger_words", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt 不能為空"}), 400
    triggers = [w.strip() for w in triggers_text.split("\n") if w.strip()]
    set_setting('system_prompt', prompt)
    set_setting('trigger_words', json.dumps(triggers, ensure_ascii=False))
    return jsonify({"status": "ok"})


@app.route("/admin/settings/reset", methods=["POST"])
def admin_settings_reset():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    set_setting('system_prompt', SYSTEM_PROMPT)
    set_setting('trigger_words', json.dumps(TRIGGER_WORDS, ensure_ascii=False))
    return jsonify({"status": "ok"})


@app.route("/admin/toggle", methods=["POST"])
def admin_toggle():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    uid = data.get("userId")
    action = data.get("action")
    if action == "pause":
        paused_users.add(uid)
    elif action == "resume":
        paused_users.discard(uid)
    return jsonify({"status": "ok"})


@app.route("/debug/logs")
def debug_logs():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(app_logs[-50:])


@app.route("/")
def health():
    return "LINE AI 客服系統運作中 ✅ v2.0-guided"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
