
import asyncio
import json
import os
import random
import re
import socket
import sys
import time
import urllib.parse
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
import pandas as pd
from stem import Signal
from stem.control import Controller
from db_manager import DB_FILE, get_db_connection, save_scraped_product, update_product_link

# ==============================================================================
# Configuration & Constants
# ==============================================================================
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CSV_INPUT_FILE = "amazon_products.csv"
OUTPUT_FILE = "scraped_products.txt"

# Batch selection settings for database rows
BATCH_LIMIT = 100     # How many products to select and scrape
BATCH_OFFSET = 0   # Starting row index/offset in database (0 = start from row 1)

MAX_RETRIES = 3

# Multi-Tor Instance & Port Settings
TOR_INSTANCES_CONFIG = [
    {"socks": 9050, "control": 9051},
    {"socks": 9052, "control": 9053},
    {"socks": 9054, "control": 9055},
    {"socks": 9056, "control": 9057},
    {"socks": 9150, "control": 9151},  # Tor Browser default fallback
]

TOR_CONTROL_PORTS = [inst["control"] for inst in TOR_INSTANCES_CONFIG]
TOR_SOCKS_PORTS = [inst["socks"] for inst in TOR_INSTANCES_CONFIG]
IMPERSONATE_PROFILES = ["chrome120", "edge101", "safari15_5"]

# Delay & Rotation Settings (in seconds)
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.5
RETRY_DELAY_MIN = 2.5
RETRY_DELAY_MAX = 4.5
BLOCKED_RETRY_DELAY = 2 * 60  # 10 minutes
PROACTIVE_ROTATION_INTERVAL = 30  # Rotate Tor exit IP every N products to prevent anti-bot blocks


# ==============================================================================
# Tor & Network Helpers
# ==============================================================================
def check_port(host: str, port: int) -> bool:
    """Check if a TCP port is open on the specified host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex((host, port)) == 0


def detect_active_tor_instances() -> List[Dict[str, Any]]:
    """Detect all active Tor SOCKS and Control port instances."""
    active = []
    for inst in TOR_INSTANCES_CONFIG:
        if check_port('127.0.0.1', inst["socks"]):
            proxy_url = f"socks5h://127.0.0.1:{inst['socks']}"
            active.append({
                "socks": inst["socks"],
                "control": inst["control"],
                "proxies": {"http": proxy_url, "https": proxy_url}
            })
    if active:
        socks_list = [str(a['socks']) for a in active]
        print(f"[TOR MULTI-INSTANCE] Detected {len(active)} active Tor SOCKS proxy instance(s) on port(s): {', '.join(socks_list)}")
    else:
        print("[WARN] No active Tor SOCKS ports detected. Running direct requests without proxy.")
    return active


async def rotate_tor_ip(control_port: Optional[int] = None) -> bool:
    """Send NEWNYM signal to a specific Tor Control Port (or all active control ports if unspecified)."""
    if 'ACTIVE_TOR_INSTANCES' in globals() and not ACTIVE_TOR_INSTANCES:
        # No Tor proxies running - skip rotation silently
        return False

    if control_port:
        target_ports = [control_port]
    elif 'ACTIVE_TOR_INSTANCES' in globals() and ACTIVE_TOR_INSTANCES:
        target_ports = [inst["control"] for inst in ACTIVE_TOR_INSTANCES if inst.get("control")]
    else:
        target_ports = TOR_CONTROL_PORTS

    rotated = False
    for ctrl_port in target_ports:
        if ctrl_port and check_port('127.0.0.1', ctrl_port):
            try:
                with Controller.from_port(port=ctrl_port) as controller:
                    controller.authenticate()
                    controller.signal(Signal.NEWNYM)
                    print(f"  [TOR IP ROTATE] Successfully sent NEWNYM signal to Control Port {ctrl_port}.")
                    rotated = True
            except Exception as e:
                print(f"  [TOR IP ROTATE WARN] Failed to rotate IP on Control Port {ctrl_port}: {e}")
        else:
            print(f"  [TOR IP ROTATE WARN] Control Port {ctrl_port} is not active or reachable.")

    if rotated:
        await asyncio.sleep(3.5)
        return True
    return False




# Initialize Active Tor Proxy Pool
ACTIVE_TOR_INSTANCES = detect_active_tor_instances()
TOR_PROXIES = ACTIVE_TOR_INSTANCES[0]["proxies"] if ACTIVE_TOR_INSTANCES else None

# Automatically scale concurrency limit based on active Tor instances
CONCURRENCY_LIMIT = max(2, len(ACTIVE_TOR_INSTANCES) * 2) if ACTIVE_TOR_INSTANCES else 2


def get_request_headers(profile: str) -> Dict[str, str]:
    """Generate authentic, fingerprint-matching browser headers."""
    if "chrome" in profile:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }
    return {
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/101.0.1210.53"
    }



# ==============================================================================
# Parser & URL Utilities
# ==============================================================================
def clean_amazon_url(raw_url: str) -> str:
    """Clean and standardize any Amazon URL (including sponsored /sspa links) to standard /dp/ASIN format."""
    if not raw_url:
        return raw_url

    decoded_url = urllib.parse.unquote(raw_url)

    asin_match = re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})', decoded_url, re.IGNORECASE)
    if not asin_match:
        asin_match = re.search(r'(?:/|\?|&|=)(B[0-9A-Z]{9})(?:/|\?|&|$)', decoded_url, re.IGNORECASE)

    if asin_match:
        asin = asin_match.group(1).upper()
        return f"https://www.amazon.in/dp/{asin}"

    return raw_url


def clean_search_query(product_name: str) -> str:
    """Truncate long product names to first 6-7 words for accurate Amazon search results."""
    cleaned = re.sub(r'[\(\)\[\]\{\}®™\+\|,]', ' ', product_name)
    words = cleaned.split()
    query_words = words[:7] if len(words) > 7 else words
    return " ".join(query_words)


def build_search_url(product_name: str) -> str:
    """Construct Amazon Search URL using a clean search query."""
    query = urllib.parse.quote_plus(clean_search_query(product_name))
    return f"https://www.amazon.in/s?k={query}"


def extract_json_ld_data(soup: BeautifulSoup) -> Dict[str, Optional[str]]:
    """Extract product metadata from JSON-LD microdata scripts if present."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            parsed = json.loads(script.string)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ["Product", "http://schema.org/Product"]:
                    title = item.get("name")
                    image = item.get("image")
                    if isinstance(image, list) and image:
                        image = image[0]
                    
                    price = None
                    offers = item.get("offers")
                    if isinstance(offers, dict):
                        price = offers.get("price")
                        currency = offers.get("priceCurrency", "")
                        if price:
                            price = f"{currency} {price}".strip() if currency else str(price)
                    elif isinstance(offers, list) and offers:
                        p = offers[0].get("price")
                        curr = offers[0].get("priceCurrency", "")
                        if p:
                            price = f"{curr} {p}".strip() if curr else str(p)

                    rating = None
                    agg_rating = item.get("aggregateRating")
                    if isinstance(agg_rating, dict):
                        rating = str(agg_rating.get("ratingValue"))

                    return {
                        "title": title,
                        "price": price,
                        "rating": rating,
                        "image": image
                    }
        except Exception:
            continue
    return {}


def parse_direct_product_html(html_text: str) -> Dict[str, Optional[str]]:
    """Parse product title, price, rating, and image from Amazon product detail HTML (DOM + JSON-LD fallbacks)."""
    soup = BeautifulSoup(html_text, 'html.parser')
    
    title_el = soup.find("span", {"id": "productTitle"})
    title = title_el.get_text(strip=True) if title_el else None

    price_el = soup.find("span", {"class": "a-offscreen"})
    price = price_el.get_text(strip=True) if price_el else None

    rating_el = soup.find("span", {"class": "a-icon-alt"})
    rating = rating_el.get_text(strip=True).split()[0] if rating_el else None

    # Product image selector
    img_el = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
    if not img_el:
        container = soup.find("div", {"id": "imgTagWrapperId"})
        if container:
            img_el = container.find("img")
    image_url = img_el.get("src") if img_el else None

    # Fix 2: JSON-LD Fallback for missing or altered DOM elements
    json_ld = extract_json_ld_data(soup)
    title = title or json_ld.get("title")
    price = price or json_ld.get("price")
    rating = rating or json_ld.get("rating")
    image_url = image_url or json_ld.get("image")

    return {
        "title": title,
        "price": price,
        "rating": rating,
        "image": image_url
    }


def parse_search_results_html(html_text: str) -> Optional[Dict[str, Optional[str]]]:
    """Parse the first organic (non-sponsored) product match from an Amazon Search results page."""
    soup = BeautifulSoup(html_text, 'html.parser')

    search_items = soup.find_all("div", {"data-component-type": "s-search-result"})
    if not search_items:
        search_items = soup.find_all("div", {"class": re.compile(r'\bs-result-item\b')})

    for item in search_items:
        classes = item.get("class", [])
        is_ad = "AdHolder" in classes or item.find("span", {"class": re.compile(r'puis-sponsored-label|s-sponsored-label')}) is not None

        link_el = None
        h2_el = item.find("h2")
        if h2_el:
            link_el = h2_el.find("a") or h2_el.find_parent("a")
        
        if not link_el:
            for a_tag in item.find_all("a", href=True):
                href = a_tag["href"]
                if any(x in href for x in ["/sspa/click", "/gp/help/", "/help/", "#customerReviews", "javascript:", "sp_atf", "sp_csd"]):
                    continue
                if "/dp/" in href or "/gp/product/" in href or re.search(r'/[A-Z0-9]{10}', href):
                    link_el = a_tag
                    break

        if not link_el or not link_el.get("href"):
            continue

        raw_href = link_el["href"]

        if is_ad or any(x in raw_href for x in ["/sspa/click", "/gp/help/", "/help/", "#customerReviews", "javascript:", "sp_atf", "sp_csd"]):
            continue

        if raw_href.startswith("/"):
            raw_href = "https://www.amazon.in" + raw_href
        clean_url = clean_amazon_url(raw_href)

        title = h2_el.get_text(strip=True) if h2_el else None
        if not title:
            title_el = item.find("span", {"class": re.compile(r'a-size-medium|a-size-base-plus')})
            title = title_el.get_text(strip=True) if title_el else None

        price_el = item.find("span", {"class": "a-offscreen"})
        price = price_el.get_text(strip=True) if price_el else None

        rating_el = item.find("span", {"class": "a-icon-alt"})
        rating = rating_el.get_text(strip=True).split()[0] if rating_el else None

        img_el = item.find("img", {"class": "s-image"})
        image_url = img_el.get("src") if img_el else None

        if title or clean_url:
            return {
                "link": clean_url,
                "title": title,
                "price": price,
                "rating": rating,
                "image": image_url
            }

    return None


# ==============================================================================
# Scraper Core Engine
# ==============================================================================
async def fetch_direct_product(
    session: AsyncSession, 
    index: int, 
    test_url: str, 
    tor_instance: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Fetch and parse direct product page using assigned Tor instance."""
    proxies = tor_instance["proxies"] if tor_instance else TOR_PROXIES
    control_port = tor_instance["control"] if tor_instance else None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            profile = random.choice(IMPERSONATE_PROFILES)
            headers = get_request_headers(profile)
            session.cookies.clear()  # Clear cookies to avoid CAPTCHA tracking cookies

            response = await session.get(test_url, impersonate=profile, headers=headers, proxies=proxies, timeout=15)
            
            if response.status_code == 404:
                return {"index": index, "is_404": True, "error": "URL not found (404)", "is_blocked": False, "url": test_url}

            response.raise_for_status()
            parsed = parse_direct_product_html(response.text)

            if not parsed["title"]:
                if attempt < MAX_RETRIES:
                    rotate_msg = "Rotating Tor IP circuit..." if ACTIVE_TOR_INSTANCES else "Retrying..."
                    print(f"Product {index+1} direct attempt {attempt} CAPTCHA block / missing title. {rotate_msg}")
                    await rotate_tor_ip(control_port)
                    await asyncio.sleep(random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX))
                    continue
                else:
                    return {"index": index, "is_404": False, "error": "CAPTCHA block", "is_blocked": True, "url": test_url}

            print(f"Product {index+1} Direct Fetch Success | Status: {response.status_code}")
            return {
                "index": index,
                "is_404": False,
                "title": parsed["title"],
                "price": parsed["price"],
                "rating": parsed["rating"],
                "image": parsed["image"],
                "is_blocked": False,
                "url": test_url
            }
        except Exception as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            is_404 = (status_code == 404)
            if is_404:
                return {"index": index, "is_404": True, "error": "URL not found (404)", "is_blocked": False, "url": test_url}
            
            if attempt < MAX_RETRIES:
                rotate_msg = "Rotating Tor IP circuit..." if ACTIVE_TOR_INSTANCES else "Retrying..."
                print(f"Product {index+1} direct attempt {attempt} error: {e}. {rotate_msg}")
                await rotate_tor_ip(control_port)
                await asyncio.sleep(random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX))
            else:
                return {"index": index, "is_404": False, "error": str(e), "is_blocked": True, "url": test_url}


async def fetch_search_product(
    session: AsyncSession, 
    index: int, 
    product_name: str, 
    search_url: str, 
    tor_instance: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Fetch search results page and extract 1st product item using assigned Tor instance."""
    proxies = tor_instance["proxies"] if tor_instance else TOR_PROXIES
    control_port = tor_instance["control"] if tor_instance else None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            profile = random.choice(IMPERSONATE_PROFILES)
            headers = get_request_headers(profile)
            session.cookies.clear()  # Clear cookies to avoid CAPTCHA tracking cookies

            response = await session.get(search_url, impersonate=profile, headers=headers, proxies=proxies, timeout=15)
            response.raise_for_status()

            match = parse_search_results_html(response.text)
            if not match:
                if attempt < MAX_RETRIES:
                    rotate_msg = "Rotating Tor IP circuit..." if ACTIVE_TOR_INSTANCES else "Retrying..."
                    print(f"Product {index+1} search attempt {attempt} CAPTCHA / No match. {rotate_msg}")
                    await rotate_tor_ip(control_port)
                    await asyncio.sleep(random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX))
                    continue
                else:
                    return {"index": index, "error": "Search page CAPTCHA / No match found", "is_blocked": True, "url": search_url}

            print(f"Product {index+1} Search Fallback Success | Found: {match['title'][:40]}...")
            return {
                "index": index,
                "title": match["title"],
                "price": match["price"],
                "rating": match["rating"],
                "image": match["image"],
                "link": match["link"],
                "is_blocked": False,
                "url": match["link"]
            }
        except Exception as e:
            if attempt < MAX_RETRIES:
                rotate_msg = "Rotating Tor IP circuit..." if ACTIVE_TOR_INSTANCES else "Retrying..."
                print(f"Product {index+1} search attempt {attempt} error: {e}. {rotate_msg}")
                await rotate_tor_ip(control_port)
                await asyncio.sleep(random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX))
            else:
                return {"index": index, "error": str(e), "is_blocked": True, "url": search_url}



async def fetch_product(
    session: AsyncSession, 
    item_data: Dict[str, Any], 
    semaphore: asyncio.Semaphore
) -> Dict[str, Any]:
    """
    Main product processor:
    1. If link exists -> standardize to /dp/ASIN format and update DB if changed -> direct fetch.
    2. If link is invalid (404) -> nullify link in DB -> fallback to search by product name.
    3. Save results to database.
    """
    index = item_data["index"]
    name = item_data.get("name") or item_data.get("raw_name", "")
    link = item_data.get("link")

    # Assign a round-robin Tor SOCKS/Control port instance across concurrent workers
    tor_instance = ACTIVE_TOR_INSTANCES[index % len(ACTIVE_TOR_INSTANCES)] if ACTIVE_TOR_INSTANCES else None

    async with semaphore:
        # Proactive Tor IP rotation every PROACTIVE_ROTATION_INTERVAL products
        if PROACTIVE_ROTATION_INTERVAL > 0 and index > 0 and index % PROACTIVE_ROTATION_INTERVAL == 0:
            control_port = tor_instance["control"] if tor_instance else None
            print(f"  [PROACTIVE ROTATION] Reached product #{index+1}. Preemptively rotating Tor IP circuit...")
            await rotate_tor_ip(control_port)

        # Small jitter delay to simulate natural browsing pace
        await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        # CASE 1: Product has an existing link
        if link and isinstance(link, str) and link.strip():
            test_url = clean_amazon_url(link)

            # If existing link is not in standard /dp/ASIN format, update DB first
            if test_url != link:
                update_product_link(name, test_url)
                print(f"Product {index+1}: Standardized link to {test_url} and updated DB.")

            result = await fetch_direct_product(session, index, test_url, tor_instance=tor_instance)

            # If valid & successfully scraped
            if not result.get("is_404"):
                if not result.get("error"):
                    save_scraped_product(
                        name=name,
                        link=test_url,
                        price=result.get("price"),
                        ratings=result.get("rating"),
                        image=result.get("image")
                    )
                return {
                    **result,
                    "name": name,
                    "raw_name": name,
                    "original_link": link,
                    "direct_link": test_url,
                    "used_fallback": False
                }

            # If link is INVALID (404) -> nullify link in DB and fall back to search!
            print(f"Product {index+1} link invalid (404). Nullifying link in DB & falling back to search for '{name[:30]}...'")
            update_product_link(name, None)

        # CASE 2: Product link is NULL or invalidated (404) -> Search Fallback
        search_url = build_search_url(name)
        search_result = await fetch_search_product(session, index, name, search_url, tor_instance=tor_instance)

        if not search_result.get("error") and search_result.get("link"):
            save_scraped_product(
                name=name,
                link=search_result.get("link"),
                price=search_result.get("price"),
                ratings=search_result.get("rating"),
                image=search_result.get("image")
            )

        return {
            **search_result,
            "name": name,
            "raw_name": name,
            "original_link": link if (link and str(link).strip()) else None,
            "direct_link_404": True if (link and str(link).strip()) else False,
            "search_url": search_url,
            "used_fallback": True
        }


async def process_blocked_retries(
    session: AsyncSession, 
    blocked_items: List[Dict[str, Any]], 
    semaphore: asyncio.Semaphore
) -> List[Dict[str, Any]]:
    """Wait before rotating IP and retrying blocked products."""
    print(f"\n[RETRY QUEUE] {len(blocked_items)} link(s) were blocked. Waiting {BLOCKED_RETRY_DELAY} seconds before retrying...")
    await asyncio.sleep(BLOCKED_RETRY_DELAY)
    await rotate_tor_ip()

    retry_tasks = [
        fetch_product(session, item, semaphore)
        for item in blocked_items
    ]
    return await asyncio.gather(*retry_tasks)


# ==============================================================================
# Data Storage & CLI Output
# ==============================================================================
def save_results_to_file(results: List[Dict[str, Any]], filepath: str) -> None:
    """Save scraped product data with full execution breakdown to a text file."""
    with open(filepath, "w", encoding="utf-8") as f:
        for idx, product in enumerate(results, 1):
            f.write(f"--- Product #{idx} ---\n")
            has_error = bool(product.get("error"))
            used_fallback = product.get("used_fallback", False)
            orig_link = product.get("original_link")
            is_404 = product.get("direct_link_404", False)

            # 1. STATUS LINE
            if not has_error:
                if used_fallback:
                    f.write("Status: SUCCESS (via Search Fallback)\n")
                else:
                    f.write("Status: SUCCESS (Direct Link)\n")
            else:
                if is_404:
                    f.write("Status: FAILED (Original Direct Link returned 404 -> Search Fallback Blocked)\n")
                elif used_fallback:
                    f.write("Status: FAILED (Search Query Blocked)\n")
                else:
                    f.write("Status: FAILED (Direct Link Blocked)\n")

            # 2. ERROR MESSAGE (IF ANY)
            if has_error:
                f.write(f"Error: {product['error']}\n")

            # 3. PRODUCT DETAILS
            f.write(f"Name/Title: {product.get('title') or product.get('raw_name') or product.get('name')}\n")
            if not has_error:
                f.write(f"Price: {product.get('price')}\n")
                f.write(f"Rating: {product.get('rating')}\n")
                f.write(f"Image: {product.get('image')}\n")

            # 4. URL METADATA BREAKDOWN
            if orig_link:
                status_note = " -> [404 Not Found]" if is_404 else ""
                f.write(f"Original Input Direct Link: {orig_link}{status_note}\n")
            
            if used_fallback:
                f.write(f"Fallback Search URL: {product.get('search_url') or product.get('url')}\n")
                if not has_error and product.get("link"):
                    f.write(f"Found Product URL: {product.get('link')}\n")
            else:
                f.write(f"URL: {product.get('url') or product.get('direct_link')}\n")

            f.write("\n")



def interactive_inspector(products_list: List[Dict[str, Any]]) -> None:
    """Terminal prompt to query product data by index interactively."""
    if not sys.stdin.isatty():
        return

    while True:
        typed = input("Enter product number to show info or 'q' to quit: ").strip()
        if typed.lower() == 'q':
            break
        if not typed.isdigit():
            print("Please enter a valid number or 'q' to quit.")
            continue
        idx = int(typed)
        if 0 < idx <= len(products_list):
            print(products_list[idx - 1])
        else:
            print("Invalid product number")


# ==============================================================================
# Main Entry Point
# ==============================================================================
async def main() -> List[Dict[str, Any]]:
    start_time = time.time()

    # Load records from SQLite database products.db (or fallback to CSV)
    if os.path.exists(DB_FILE):
        print(f"Loading products from SQLite database '{DB_FILE}' (Offset: {BATCH_OFFSET}, Limit: {BATCH_LIMIT})...")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, link, price, ratings, image FROM products LIMIT ? OFFSET ?", 
            (BATCH_LIMIT, BATCH_OFFSET)
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    else:
        print(f"Loading products from CSV '{CSV_INPUT_FILE}' (Offset: {BATCH_OFFSET}, Limit: {BATCH_LIMIT})...")
        df = pd.read_csv(CSV_INPUT_FILE).iloc[BATCH_OFFSET:BATCH_OFFSET + BATCH_LIMIT]
        rows = df.to_dict(orient="records")

    items_to_process = [
        {
            "index": i,
            "id": r.get("id"),
            "name": r["name"],
            "link": r.get("link")
        }
        for i, r in enumerate(rows)
    ]

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async with AsyncSession() as session:
        # Initial batch fetch
        tasks = [
            fetch_product(session, item, semaphore)
            for item in items_to_process
        ]
        results = await asyncio.gather(*tasks)

        # Retry blocked items if any
        blocked_items = [p for p in results if p.get("is_blocked")]
        if blocked_items:
            retry_results = await process_blocked_retries(session, blocked_items, semaphore)
            results_dict = {p["index"]: p for p in results}
            for retried in retry_results:
                results_dict[retried["index"]] = retried
            results = list(results_dict.values())

    # Sort results in original index order
    results.sort(key=lambda x: x["index"])

    elapsed_time = time.time() - start_time
    save_results_to_file(results, OUTPUT_FILE)

    print(f"\nSuccessfully saved scraped product information to {OUTPUT_FILE}")
    print(f"Total Extraction Time: {elapsed_time:.2f} seconds")
    return results


if __name__ == "__main__":
    products = asyncio.run(main())
    interactive_inspector(products)
