import sys
import time
from curl_cffi import requests
from stem import Signal
from stem.control import Controller

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Try Control Port (9051 for Linux VPS / 9151 for Tor Browser)
CONTROL_PORTS = [9051, 9151]
SOCKS_PORTS = [9050, 9150]

def get_current_tor_ip(socks_port):
    proxy_url = f"socks5h://127.0.0.1:{socks_port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        r = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=10)
        if r.status_code == 200:
            return r.json().get('ip')
    except Exception as e:
        return f"Error: {e}"
    return "Unknown"

def request_new_ip(control_port):
    try:
        with Controller.from_port(port=control_port) as controller:
            # On Linux VPS, default authentication uses cookie file / blank
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print(f"🔄 Sent NEWNYM signal to Tor Control Port {control_port}")
            return True
    except Exception as e:
        print(f"Could not authenticate on Control Port {control_port}: {e}")
        return False

print("=== TOR NEWNYM IP ROTATION TEST ===\n")

# 1. Get initial IP
active_socks_port = 9150
initial_ip = get_current_tor_ip(active_socks_port)
print(f"1. Initial Tor Exit IP: {initial_ip}")

# 2. Trigger NEWNYM signal
print("\nSending NEWNYM signal to request a new Tor circuit...")
for ctrl_port in CONTROL_PORTS:
    if request_new_ip(ctrl_port):
        break

# Wait 3-5 seconds for Tor to establish new exit circuit
print("Waiting 5 seconds for new Tor circuit to build...")
time.sleep(5)

# 3. Get new IP after NEWNYM
new_ip = get_current_tor_ip(active_socks_port)
print(f"2. New Tor Exit IP:     {new_ip}\n")

if initial_ip != new_ip and not new_ip.startswith("Error"):
    print("🎉 SUCCESS! Tor IP successfully rotated to a new exit node!")
else:
    print("Note: Control Port requires password authentication or Tor circuit was reused.")
