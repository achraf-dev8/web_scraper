import sys
import socket
from curl_cffi import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def check_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex((host, port)) == 0

print("--- Tor Connection Diagnostic ---")
print(f"Checking Port 9050 (Standalone Tor): {'OPEN' if check_port('127.0.0.1', 9050) else 'CLOSED'}")
print(f"Checking Port 9150 (Tor Browser):    {'OPEN' if check_port('127.0.0.1', 9150) else 'CLOSED'}\n")

PORTS = [9150, 9050]
connected = False

for port in PORTS:
    if not check_port('127.0.0.1', port):
        continue

    for scheme in ["socks5h", "socks5"]:
        proxy_url = f"{scheme}://127.0.0.1:{port}"
        proxies = {"http": proxy_url, "https": proxy_url}
        
        print(f"Testing {proxy_url}...")
        try:
            res = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=10)
            if res.status_code == 200:
                ip_data = res.json()
                print(f"✅ SUCCESS! Connected to Tor ({proxy_url})")
                print(f"   Tor Public IP: {ip_data.get('ip')}\n")
                
                # Test Amazon fetch via Tor
                amazon_url = "https://www.amazon.in/dp/B0883LQJ6B"
                print(f"Testing Amazon fetch via Tor: {amazon_url}")
                amz_res = requests.get(amazon_url, impersonate="chrome120", proxies=proxies, timeout=15)
                print(f"Amazon Response Code: {amz_res.status_code}")
                print(f"Amazon HTML Size: {len(amz_res.text)} bytes")
                if "productTitle" in amz_res.text:
                    print("🎉 PASSED: Successfully fetched Amazon product page via Tor!")
                else:
                    print("⚠️ Note: Current Tor exit node was challenged by Amazon. Retrying or changing circuit will get a clean IP.")
                connected = True
                break
        except Exception as e:
            print(f"   Failed using {scheme}: {e}")
            
    if connected:
        break

if not connected:
    print("\n❌ Tor port is not open yet.")
    print("If using Tor Browser: click 'CONNECT' on the startup screen.")
