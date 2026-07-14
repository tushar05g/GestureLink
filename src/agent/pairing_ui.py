import webview
import json
import urllib.request
import urllib.error
import socket
import os
import platform
import uuid

FIREBASE_URL = "https://gesturelink-5db9c-default-rtdb.firebaseio.com"

# The HTML for the Pairing Window
HTML = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #0d1117;
            color: #c9d1d9;
            margin: 0;
            padding: 30px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            box-sizing: border-box;
            text-align: center;
        }
        h2 { color: #58a6ff; margin-bottom: 10px; }
        p { color: #8b949e; margin-bottom: 25px; font-size: 0.95rem; }
        .pin-container {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .pin-input {
            width: 45px;
            height: 55px;
            font-size: 1.5rem;
            text-align: center;
            background: #161b22;
            border: 2px solid #30363d;
            border-radius: 8px;
            color: #fff;
            font-weight: bold;
        }
        .pin-input:focus {
            border-color: #58a6ff;
            outline: none;
            box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.3);
        }
        button {
            background-color: #238636;
            color: white;
            border: none;
            padding: 12px 24px;
            font-size: 1rem;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            transition: 0.2s;
            width: 100%;
            max-width: 250px;
        }
        button:hover { background-color: #2ea043; }
        button:disabled { background-color: #30363d; cursor: not-allowed; color: #8b949e; }
        #status {
            margin-top: 15px;
            font-size: 0.9rem;
            height: 20px;
        }
        .error { color: #f85149; }
        .success { color: #3fb950; }
    </style>
</head>
<body>
    <h2>Link Agent to Hub</h2>
    <p>Enter the 6-digit Pairing Code generated from your GestureLink Hub Dashboard.</p>
    
    <div class="pin-container" id="pin-boxes">
        <input type="text" class="pin-input" maxlength="1" id="p1" autofocus>
        <input type="text" class="pin-input" maxlength="1" id="p2">
        <input type="text" class="pin-input" maxlength="1" id="p3">
        <span style="font-size:2rem;line-height:55px;color:#8b949e;">-</span>
        <input type="text" class="pin-input" maxlength="1" id="p4">
        <input type="text" class="pin-input" maxlength="1" id="p5">
        <input type="text" class="pin-input" maxlength="1" id="p6">
    </div>
    
    <button id="submit-btn" onclick="submitPin()">Link PC</button>
    <div id="status"></div>

    <script>
        // Auto focus next input
        const inputs = document.querySelectorAll('.pin-input');
        inputs.forEach((input, index) => {
            input.addEventListener('input', () => {
                if(input.value.length === 1 && index < inputs.length - 1) {
                    inputs[index + 1].focus();
                }
            });
            input.addEventListener('keydown', (e) => {
                if(e.key === 'Backspace' && input.value.length === 0 && index > 0) {
                    inputs[index - 1].focus();
                }
                if(e.key === 'Enter') submitPin();
            });
        });

        async function submitPin() {
            let pin = '';
            inputs.forEach(i => pin += i.value);
            if(pin.length !== 6) {
                document.getElementById('status').className = 'error';
                document.getElementById('status').innerText = 'Please enter all 6 digits.';
                return;
            }
            
            const btn = document.getElementById('submit-btn');
            btn.disabled = true;
            btn.innerText = 'Verifying...';
            
            try {
                const result = await pywebview.api.verify_pin(pin);
                if(result.success) {
                    document.getElementById('status').className = 'success';
                    document.getElementById('status').innerText = 'Linked successfully! You can close this window.';
                    btn.innerText = 'Connected';
                    setTimeout(() => pywebview.api.close_window(), 2000);
                } else {
                    document.getElementById('status').className = 'error';
                    document.getElementById('status').innerText = result.error;
                    btn.disabled = false;
                    btn.innerText = 'Link PC';
                }
            } catch(e) {
                document.getElementById('status').className = 'error';
                document.getElementById('status').innerText = 'An error occurred connecting to backend.';
                btn.disabled = false;
                btn.innerText = 'Link PC';
            }
        }
    </script>
</body>
</html>
"""

def get_config_path():
    return os.path.join(os.path.expanduser("~"), ".gesturelink_agent.json")

def load_agent_config():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_agent_config(uid, agent_id):
    path = get_config_path()
    with open(path, 'w') as f:
        json.dump({"uid": uid, "agent_id": agent_id}, f)

class Api:
    def __init__(self, window):
        self.window = window

    def verify_pin(self, pin):
        try:
            req = urllib.request.Request(f"{FIREBASE_URL}/pairing_codes/{pin}.json")
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                
            if not data or 'uid' not in data:
                return {"success": False, "error": "Invalid or expired pairing code."}
            
            uid = data['uid']
            
            # 1. Save config locally
            agent_id = str(uuid.uuid4())
            save_agent_config(uid, agent_id)
            
            # 2. Register Agent in Firebase
            hostname = socket.gethostname()
            os_name = platform.system()
            agent_data = {
                "hostname": hostname,
                "os": os_name,
                "status": "online"
            }
            
            req_put = urllib.request.Request(
                f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}.json",
                data=json.dumps(agent_data).encode('utf-8'),
                method='PUT'
            )
            with urllib.request.urlopen(req_put, timeout=5) as res2:
                pass
                
            # 3. Delete the pairing code (burn it)
            try:
                req_del = urllib.request.Request(
                    f"{FIREBASE_URL}/pairing_codes/{pin}.json",
                    method='DELETE'
                )
                with urllib.request.urlopen(req_del, timeout=5) as res3:
                    pass
            except Exception as delete_error:
                print(f"Could not delete pairing code (likely due to Firebase rules): {delete_error}")

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Network error: {str(e)}"}

    def close_window(self):
        self.window.destroy()

def start_pairing_ui():
    window = webview.create_window(
        'GestureLink Agent Pairing',
        html=HTML,
        width=400,
        height=450,
        resizable=False,
        frameless=False,
        on_top=True
    )
    api = Api(window)
    window.expose(api.verify_pin, api.close_window)
    webview.start()

if __name__ == '__main__':
    start_pairing_ui()
