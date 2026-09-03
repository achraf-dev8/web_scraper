import threading
import queue
import requests
import pandas as pd

q = queue.Queue()
valid_proxies = []
output_messages = []

df = pd.read_csv("proxies.csv")

for _, row in df.iterrows():
    proxy = f"http://{row['ip']}:{row['port']}"
    q.put(proxy)

def check_proxy():
    global q
    while not q.empty():
        proxy = q.get()
        try:
            response = requests.get('https://ipinfo.io/json', 
                proxies={'http': proxy, 'https': proxy}, timeout=10)
        except Exception as e:
            message = f"{proxy} is not working, error: {e}"
            print(message)
            output_messages.append(message)
            continue

        if response.status_code == 200:
            valid_proxies.append(proxy)
            message = f"{proxy} is working"
            print(message)
            output_messages.append(message)

for _ in range(10):
    t = threading.Thread(target=check_proxy)
    t.start()

#create new csv file with only valid proxies
for valid_proxy in valid_proxies:
    #create dataframe from valid proxies
    df_valid = pd.DataFrame(valid_proxies)
    #save dataframe to csv file
    df_valid.to_csv("valid_proxies.csv", index=False)

for output_message in output_messages:
    #save output messages to text file
    with open("output_messages.txt", "a") as f:
        f.write(output_message + "\n")
    print(output_message)





