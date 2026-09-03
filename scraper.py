from bs4 import BeautifulSoup
from curl_cffi import requests
import pandas as pd
import sys
import re
import random
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# prepare data
df = pd.read_csv("amazon_products.csv")

df = df.head(200).tail(10)


products_list = []

IMPERSONATE_PROFILES = ["chrome120", "edge101", "safari15_5"]

for i, raw_url in enumerate(df["link"]):
    # Clean URL to standard /dp/ASIN format without stale search parameters
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', raw_url)
    test_url = f"https://www.amazon.in/dp/{asin_match.group(1)}" if asin_match else raw_url

    product_data = None
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            profile = random.choice(IMPERSONATE_PROFILES)
            headers = {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            
            response = requests.get(test_url, impersonate=profile, headers=headers, timeout=12)
            response.raise_for_status()

            # Check if response is Amazon CAPTCHA block (size ~3793 or no product title)
            soup = BeautifulSoup(response.text, 'html.parser')
            title = soup.find("span", {"id": "productTitle"})

            if not title:
                if attempt < max_retries:
                    print(f"Product {i+1} attempt {attempt} got CAPTCHA block. Retrying...")
                    time.sleep(random.uniform(2, 5))
                    continue
                else:
                    product_data = {
                        "error": "Could not parse product title (possibly blocked)",
                        "url": test_url
                    }
                    with open("debug.html", "w", encoding="utf-8") as f:
                        f.write(response.text)
                    break

            price = soup.find("span", {"class": "a-offscreen"})
            price = price.get_text(strip=True) if price else None

            rating = soup.find("span", {"class": "a-icon-alt"})
            rating = rating.get_text(strip=True) if rating else None
            rating = rating.split()[0] if rating else None

            product_data = {
                "title": title.get_text(strip=True),
                "price": price,
                "rating": rating,
                "url": test_url
            }
            print("STATUS:", response.status_code)
            print("FINAL URL:", response.url)
            print("HTML SIZE:", len(response.text))
            break

        except Exception as e:
            if attempt < max_retries:
                time.sleep(random.uniform(2, 4))
            else:
                product_data = {
                    "error": str(e),
                    "url": test_url
                }

    products_list.append(product_data)
    print("product", i+1, "is done")
    time.sleep(random.uniform(2, 5))


# Save collected data to scraped_products.txt file
with open("scraped_products.txt", "w", encoding="utf-8") as f:
    for idx, product in enumerate(products_list, 1):
        f.write(f"--- Product #{idx} ---\n")
        if "error" in product:
            f.write(f"Error: {product['error']}\n")
            f.write(f"URL: {product['url']}\n")
        else:
            f.write(f"Title: {product['title']}\n")
            f.write(f"Price: {product['price']}\n")
            f.write(f"Rating: {product['rating']}\n")
            f.write(f"URL: {product['url']}\n")
        f.write("\n")

print("Successfully saved scraped product information to scraped_products.txt")


# show collected data
while True:
    typed = input("Enter product number to show info or 'q' to quit: ").strip()
    if typed.lower() == 'q':
        break
    if not typed.isdigit():
        print("Please enter a valid number or 'q' to quit.")
        continue
    typed = int(typed)
    if 0 < typed <= len(products_list):
        print(products_list[typed-1])
    else:
        print("Invalid product number")