import time
import threading
import random
import requests
import os
import sys
from flask import Flask, jsonify, render_template_string, request
from dotenv import load_dotenv

# ===================================================================
# CẤU HÌNH VÀ BIẾN TOÀN CỤC
# ===================================================================

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")

if not TOKEN:
    print("LỖI: Vui lòng cung cấp DISCORD_TOKEN trong biến môi trường (.env).", flush=True)
    sys.exit(1)

lock = threading.RLock()

# Các biến cài đặt spam
spam_panels = []
panel_id_counter = 0

# Header giả mạo trình duyệt (Tuyệt chiêu vượt Cloudflare của bạn)
SPOOFED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Authorization": TOKEN.strip(),
    "Content-Type": "application/json"
}

# ===================================================================
# HÀM LƯU/TẢI CÀI ĐẶT JSON
# ===================================================================

def save_settings():
    with lock:
        if not JSONBIN_API_KEY or not JSONBIN_BIN_ID: return False
        settings_to_save = {'spam_panels': spam_panels, 'panel_id_counter': panel_id_counter}
        headers = {'Content-Type': 'application/json', 'X-Master-Key': JSONBIN_API_KEY}
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
        try:
            req = requests.put(url, json=settings_to_save, headers=headers, timeout=10)
            if req.status_code == 200:
                print("[SETTINGS] Đã lưu cài đặt lên JSONBin.io.", flush=True)
                return True
        except Exception as e: print(f"[SETTINGS] LỖI lưu cài đặt: {e}", flush=True)
        return False

def load_settings():
    global spam_panels, panel_id_counter
    with lock:
        if not JSONBIN_API_KEY or not JSONBIN_BIN_ID: return False
        headers = {'X-Master-Key': JSONBIN_API_KEY, 'X-Bin-Meta': 'false'}
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}/latest"
        try:
            req = requests.get(url, headers=headers, timeout=10)
            if req.status_code == 200:
                settings = req.json()
                if settings and isinstance(settings, dict):
                    spam_panels = settings.get('spam_panels', [])
                    panel_id_counter = settings.get('panel_id_counter', 0)
                    if spam_panels:
                        panel_id_counter = max(panel_id_counter, max(p.get('id', -1) for p in spam_panels) + 1)
                    print("[SETTINGS] Đã tải cài đặt từ JSONBin.io.", flush=True)
                    return True
        except Exception as e: print(f"[SETTINGS] LỖI tải cài đặt: {e}.", flush=True)
        return False

def get_new_random_delay(panel):
    mode = panel.get('delay_mode', 'minutes')
    if mode == 'seconds':
        min_s = panel.get('delay_min_seconds', 240)
        max_s = panel.get('delay_max_seconds', 300)
        if min_s > max_s: min_s, max_s = max_s, min_s
        return random.uniform(min_s, max_s)
    else: 
        min_m = panel.get('delay_min_minutes', 4)
        max_m = panel.get('delay_max_minutes', 5)
        if min_m > max_m: min_m, max_m = max_m, min_m
        return (random.randint(min_m, max_m) * 60) + random.randint(1, 15)

# ===================================================================
# LOGIC GỬI TIN NHẮN (HTTP POST RAW)
# ===================================================================

def send_message_http(channel_id, content):
    """Gửi tin nhắn qua API HTTP thay vì dùng discord.py để lách Cloudflare"""
    try:
        payload = {"content": str(content)}
        url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
        res = requests.post(url, headers=SPOOFED_HEADERS, json=payload, timeout=10)
        
        if 200 <= res.status_code < 300:
            print(f"[SPAM BOT] SUCCESS: Đã gửi tin nhắn tới {channel_id}", flush=True)
            return True
        elif res.status_code == 429:
            retry_after = res.json().get('retry_after', 5)
            print(f"[SPAM BOT] RATE LIMIT: Bị hạn chế, chờ {retry_after}s...", flush=True)
        else:
            print(f"[SPAM BOT] LỖI ({res.status_code}): {res.text}", flush=True)
    except Exception as e:
        print(f"[SPAM BOT] LỖI KẾT NỐI: {e}", flush=True)
    return False

def spam_loop_thread():
    """Luồng chạy ngầm để kiểm tra và gửi tin nhắn"""
    print("[SPAM BOT] Luồng Spam HTTP đã khởi động.", flush=True)
    while True:
        current_time = time.time()
        with lock:
            panels_to_process = list(spam_panels)
        
        for panel in panels_to_process:
            if panel.get('is_active') and panel.get('channel_id') and panel.get('message') and current_time >= panel.get('next_spam_time', 0):
                channel_id = str(panel['channel_id']).strip()
                
                # Gọi hàm gửi HTTP
                success = send_message_http(channel_id, panel['message'])
                
                with lock:
                    for p in spam_panels:
                        if p['id'] == panel['id']:
                            if success:
                                next_delay = get_new_random_delay(p)
                                p['next_spam_time'] = time.time() + next_delay
                                print(f"[SPAM BOT] Panel {p['id']} hẹn giờ gửi tiếp sau {next_delay:.2f}s.", flush=True)
                            else:
                                # Nếu lỗi (như 429), delay tạm 60s rồi thử lại
                                p['next_spam_time'] = time.time() + 60
                            save_settings()
                            break
                time.sleep(1.5) # Delay nhẹ giữa các lệnh gửi để tránh dồn dập
        time.sleep(1) # Nghỉ 1s mỗi vòng lặp

# ===================================================================
# WEB SERVER (FLASK)
# ===================================================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8"> <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Discord Auto Spam Control (HTTP Mode)</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #121212; color: #e0e0e0; display: flex; flex-direction: column; align-items: center; gap: 20px; padding: 20px;}
        h1 { color: #bb86fc; margin-top: 0; } 
        button { background-color: #bb86fc; color: #121212; border: none; padding: 12px 24px; font-size: 1em; border-radius: 5px; cursor: pointer; font-weight: bold; }
        button:hover { background-color: #a050f0; }
        .input-group { display: flex; flex-direction: column; gap: 5px; } .input-group label { text-align: left; font-size: 0.9em; color: #aaa; }
        .spam-controls { display: flex; flex-direction: column; gap: 20px; width: 100%; max-width: 840px; background-color: #1e1e1e; padding: 20px; border-radius: 10px; }
        #panel-container { display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 20px; width: 100%; }
        .spam-panel { background-color: #2a2a2a; padding: 20px; border-radius: 10px; display: flex; flex-direction: column; gap: 15px; border-left: 5px solid #333; }
        .spam-panel.active { border-left-color: #03dac6; }
        .spam-panel input, .spam-panel textarea { width: 100%; box-sizing: border-box; border: 1px solid #444; background-color: #333; color: #eee; padding: 10px; border-radius: 5px; }
        .spam-panel-controls { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .delete-btn { background-color: #cf6679 !important; }
        .add-panel-btn { width: 100%; padding: 15px; background-color: rgba(3, 218, 198, 0.2); border: 2px dashed #03dac6; color: #03dac6; cursor: pointer;}
        .timer { font-size: 0.9em; color: #888; text-align: right; }
        .save-status { position: fixed; top: 10px; right: 10px; padding: 10px; border-radius: 5px; display: none; }
        .save-success { background-color: #03dac6; color: #121212; }
        .save-error { background-color: #cf6679; color: #fff; }
        .delay-range-group { display: flex; align-items: center; gap: 5px; } .delay-range-group input { text-align: center; }
        .mode-selector { display: flex; gap: 10px; background-color: #333; padding: 5px; border-radius: 5px; }
        .mode-selector label { cursor: pointer; padding: 5px 10px; border-radius: 5px; user-select: none;}
        .mode-selector input { display: none; } .mode-selector input:checked + label { background-color: #bb86fc; color: #121212; }
        .delay-inputs { display: none; } .delay-inputs.visible { display: flex; flex-direction: column; gap: 5px; }
    </style>
</head>
<body>
    <div id="saveStatus" class="save-status"></div>
    <h1>Trình Điều Khiển Spam (HTTP Bypass Limit)</h1>
    
    <div class="spam-controls">
        <div id="panel-container"></div>
        <button class="add-panel-btn" onclick="addPanel()">+ Thêm Bảng Spam</button>
    </div>

    <script>
        function showSaveStatus(msg, isSuccess) {
            const status = document.getElementById('saveStatus');
            status.textContent = msg;
            status.className = 'save-status ' + (isSuccess ? 'save-success' : 'save-error');
            status.style.display = 'block';
            setTimeout(() => status.style.display = 'none', 2000);
        }
        
        async function apiCall(endpoint, method = 'POST', body = null) {
            const options = { method, headers: {'Content-Type': 'application/json'} };
            if (body) options.body = JSON.stringify(body);
            const res = await fetch(endpoint, options);
            const result = await res.json();
            if (result.save_status !== undefined) showSaveStatus(result.save_status ? 'Đã lưu' : 'Lỗi lưu', result.save_status);
            return result;
        }
        
        function createPanelElement(panel) {
            const div = document.createElement('div');
            div.className = `spam-panel ${panel.is_active ? 'active' : ''}`; 
            const isMinutesMode = panel.delay_mode !== 'seconds';
            let countdown = (panel.is_active && panel.next_spam_time) ? Math.max(0, Math.ceil(panel.next_spam_time - (Date.now() / 1000))) : 0;

            div.innerHTML = `
                <div class="input-group"><label>Nội dung</label><textarea class="message-input">${panel.message}</textarea></div>
                <div class="input-group"><label>ID Kênh</label><input type="text" class="channel-input" value="${panel.channel_id}"></div>
                <div class="input-group">
                    <div class="mode-selector">
                        <input type="radio" id="mode-seconds-${panel.id}" name="mode-${panel.id}" value="seconds" ${!isMinutesMode ? 'checked' : ''}><label for="mode-seconds-${panel.id}">Giây</label>
                        <input type="radio" id="mode-minutes-${panel.id}" name="mode-${panel.id}" value="minutes" ${isMinutesMode ? 'checked' : ''}><label for="mode-minutes-${panel.id}">Phút</label>
                    </div>
                </div>
                <div class="delay-inputs delay-inputs-seconds ${!isMinutesMode ? 'visible' : ''}">
                    <div class="delay-range-group"><input type="number" class="delay-input-min-seconds" value="${panel.delay_min_seconds || 240}"><span>-</span><input type="number" class="delay-input-max-seconds" value="${panel.delay_max_seconds || 300}"></div>
                </div>
                <div class="delay-inputs delay-inputs-minutes ${isMinutesMode ? 'visible' : ''}">
                    <div class="delay-range-group"><input type="number" class="delay-input-min-minutes" value="${panel.delay_min_minutes || 4}"><span>-</span><input type="number" class="delay-input-max-minutes" value="${panel.delay_max_minutes || 5}"></div>
                </div>
                <div class="spam-panel-controls">
                    <button class="toggle-btn">${panel.is_active ? 'DỪNG' : 'CHẠY'}</button>
                    <button class="delete-btn">XÓA</button>
                </div>
                <div class="timer">Gửi tiếp trong: ${panel.is_active ? countdown + 's' : '...'}</div>
            `;
            
            const getPanelData = () => ({ 
                ...panel, 
                message: div.querySelector('.message-input').value, channel_id: div.querySelector('.channel-input').value, 
                delay_mode: div.querySelector('input[name="mode-' + panel.id + '"]:checked').value,
                delay_min_seconds: parseInt(div.querySelector('.delay-input-min-seconds').value)||240, 
                delay_max_seconds: parseInt(div.querySelector('.delay-input-max-seconds').value)||300,
                delay_min_minutes: parseInt(div.querySelector('.delay-input-min-minutes').value)||4, 
                delay_max_minutes: parseInt(div.querySelector('.delay-input-max-minutes').value)||5
            });
            
            div.querySelector('.toggle-btn').addEventListener('click', () => apiCall('/api/panel/update', 'POST', { ...getPanelData(), is_active: !panel.is_active }).then(fetchPanels));
            div.querySelector('.delete-btn').addEventListener('click', () => { if (confirm('Xóa?')) apiCall('/api/panel/delete', 'POST', { id: panel.id }).then(fetchPanels); });
            ['message-input', 'channel-input', 'delay-input-min-seconds', 'delay-input-max-seconds', 'delay-input-min-minutes', 'delay-input-max-minutes'].forEach(cls => {
                div.querySelector('.' + cls).addEventListener('change', () => apiCall('/api/panel/update', 'POST', getPanelData()));
            });
            div.querySelectorAll('input[name="mode-' + panel.id + '"]').forEach(radio => {
                radio.addEventListener('change', (e) => {
                    div.querySelector('.delay-inputs-seconds').classList.toggle('visible', e.target.value === 'seconds');
                    div.querySelector('.delay-inputs-minutes').classList.toggle('visible', e.target.value === 'minutes');
                    apiCall('/api/panel/update', 'POST', getPanelData());
                });
            });
            return div;
        }
        
        async function fetchPanels() {
            if (document.activeElement && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
            const data = await apiCall('/api/panels', 'GET');
            const container = document.getElementById('panel-container'); 
            if (container) {
                container.innerHTML = '';
                if (data.panels) data.panels.forEach(p => container.appendChild(createPanelElement(p)));
            }
        }
        
        async function addPanel() { await apiCall('/api/panel/add'); fetchPanels(); }
        document.addEventListener('DOMContentLoaded', () => { fetchPanels(); setInterval(fetchPanels, 1000); });
    </script>
</body>
</html>
"""

@app.route("/")
def index(): return render_template_string(HTML_TEMPLATE)

@app.route("/api/panels", methods=['GET'])
def get_panels():
    with lock: return jsonify({"panels": spam_panels})

@app.route("/api/panel/add", methods=['POST'])
def add_panel():
    global panel_id_counter
    with lock:
        new_panel = { 
            "id": panel_id_counter, "message": "", "channel_id": "", "delay_mode": "minutes",
            "delay_min_minutes": 4, "delay_max_minutes": 5, "delay_min_seconds": 240, "delay_max_seconds": 300,
            "is_active": False, "next_spam_time": 0 
        }
        spam_panels.append(new_panel)
        panel_id_counter += 1
        save_result = save_settings()
    return jsonify({"status": "ok", "new_panel": new_panel, "save_status": save_result})

@app.route("/api/panel/update", methods=['POST'])
def update_panel():
    data = request.get_json()
    with lock:
        for panel in spam_panels:
            if panel['id'] == data['id']:
                if data.get('is_active') and not panel.get('is_active'): data['next_spam_time'] = time.time()
                elif not data.get('is_active'): data['next_spam_time'] = 0
                panel.update(data)
                break
        save_result = save_settings()
    return jsonify({"status": "ok", "save_status": save_result})

@app.route("/api/panel/delete", methods=['POST'])
def delete_panel():
    data = request.get_json()
    with lock:
        spam_panels[:] = [p for p in spam_panels if p['id'] != data['id']]
        save_result = save_settings()
    return jsonify({"status": "ok", "save_status": save_result})

if __name__ == "__main__":
    load_settings()
    
    # Chạy vòng lặp spam ở một luồng riêng
    threading.Thread(target=spam_loop_thread, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    print(f"[SERVER] Khởi động Web Server tại cổng {port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
