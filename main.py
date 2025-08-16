import os
import re
import json
import time
import threading
import requests
from bs4 import BeautifulSoup
from flask import Flask

# === Config from env ===
BOT_TOKEN       = os.environ["BOT_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
DEFAULT_MAX     = int(os.environ.get("MAX_PRICE", "800"))
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))  # offers check
CMD_POLL_SEC    = int(os.environ.get("CMD_POLL_SEC", "3"))      # command poll
VERBOSE         = int(os.environ.get("VERBOSE", "1"))

runtime_max_price = DEFAULT_MAX

SEARCH_URLS = [
    # 1-комн. квартиры
    "https://www.wg-gesucht.de/1-zimmer-wohnungen-in-Muenchen.90.1.1.0.html",
    # Комнаты в WG
    "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html"
]

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "de,en;q=0.9,ru;q=0.8",
    "Referer": "https://www.wg-gesucht.de/",
}

seen_ids = set()
last_update_id = 0

# === Flask for Render health ===
app = Flask(__name__)
@app.get("/")
def home():
    return "WG bot is running"
@app.get("/health")
def health():
    return "ok"

def log(*a):
    if VERBOSE:
        print("[LOG]", *a)

# === Telegram helpers ===
def send_message(text: str):
    try:
        requests.post(f"{TG_API}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
                      timeout=20)
    except Exception as e:
        log("send_message error:", e)

# === Parsing helpers ===
def fetch_html(url: str) -> str:
    s = requests.Session()
    # Accept basic cookie to avoid consent walls
    try:
        s.get("https://www.wg-gesucht.de/", headers=HEADERS, timeout=20)
    except Exception:
        pass
    r = s.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def extract_offers_from_jsonld(soup: BeautifulSoup):
    offers = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            name = obj.get("name") or obj.get("headline")
            url = obj.get("url")
            price = None
            if isinstance(obj.get("offers"), dict):
                price = obj["offers"].get("price") or obj["offers"].get("priceSpecification", {}).get("price")
            elif "price" in obj:
                price = obj.get("price")
            if name and url and price:
                try:
                    price_num = int(re.search(r"(\d+)", str(price)).group(1))
                except Exception:
                    continue
                offers.append({"id": url, "title": name, "price": price_num, "url": url})
    return offers

def extract_offers_from_cards(soup: BeautifulSoup):
    offers = []

    # 1) Блоки с id "liste-details-ad-"
    for ad in soup.find_all("div", id=re.compile(r"liste-details-ad-\d+")):
        ad_id = ad.get("id")
        title_tag = ad.find("h3")
        link_tag = ad.find("a", href=True)
        price_text = ad.get_text(" ", strip=True)
        m = re.search(r"(\d{2,5})\s*€", price_text.replace("\xa0"," "))
        if not (ad_id and title_tag and link_tag and m):
            continue
        price = int(m.group(1))
        url = link_tag["href"]
        if url.startswith("/"):
            url = "https://www.wg-gesucht.de" + url
        offers.append({"id": ad_id, "title": title_tag.get_text(strip=True), "price": price, "url": url})

    # 2) Fallback: article[data-id]
    if not offers:
        for ad in soup.find_all(["article","div"], attrs={"data-id": True}):
            ad_id = ad.get("data-id")
            title_tag = ad.find(["h3","h2"])
            link_tag = ad.find("a", href=True)
            txt = ad.get_text(" ", strip=True)
            m = re.search(r"(\d{2,5})\s*€", txt.replace("\xa0"," "))
            if not (ad_id and title_tag and link_tag and m):
                continue
            price = int(m.group(1))
            url = link_tag["href"]
            if url.startswith("/"):
                url = "https://www.wg-gesucht.de" + url
            offers.append({"id": ad_id, "title": title_tag.get_text(strip=True), "price": price, "url": url})

    # 3) Anchors as last resort
    if not offers:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.search(r"(wg-zimmer|1-zimmer-wohnungen?|wohnungen)", href):
                continue
            parent_text = a.find_parent().get_text(" ", strip=True) if a.find_parent() else a.get_text(" ", strip=True)
            m = re.search(r"(\d{2,5})\s*€", parent_text.replace("\xa0"," "))
            if not m:
                continue
            price = int(m.group(1))
            title = a.get_text(strip=True) or "Angebot"
            url = href
            if url.startswith("/"):
                url = "https://www.wg-gesucht.de" + url
            offers.append({"id": url, "title": title, "price": price, "url": url})

    return offers

def fetch_offers(max_price: int):
    collected = []
    for url in SEARCH_URLS:
        try:
            html = fetch_html(url)
            soup = BeautifulSoup(html, "html.parser")
            offers = extract_offers_from_cards(soup)
            if not offers:
                offers = extract_offers_from_jsonld(soup)
            log(f"Parsed {len(offers)} offers from {url}")
            for o in offers:
                if o["price"] <= max_price:
                    collected.append(o)
        except Exception as e:
            log("fetch_offers error:", e)
    unique = {}
    for o in collected:
        key = o["id"]
        if key not in unique:
            unique[key] = o
    return list(unique.values())

# === Bot logic ===
def push_new_offers():
    sent = 0
    offers = fetch_offers(runtime_max_price)
    for o in offers:
        if o["id"] in seen_ids:
            continue
        seen_ids.add(o["id"])
        msg = f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}"
        send_message(msg)
        sent += 1
    if VERBOSE: log(f"Auto-pushed {sent} new offers")
    return sent

def handle_updates():
    global last_update_id, runtime_max_price
    try:
        params = {}
        if last_update_id:
            params["offset"] = last_update_id + 1
        resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=20).json()
        for upd in resp.get("result", []):
            last_update_id = max(last_update_id, upd.get("update_id", 0))
            msg = upd.get("message") or {}
            if not msg:
                continue
            chat_id = msg.get("chat", {}).get("id")
            if str(chat_id) != str(CHAT_ID):
                continue
            text = (msg.get("text") or "").strip()

            if text.lower().startswith("/help"):
                send_message(
                    "🤖 Команды:\n"
                    "/all — показать все актуальные объявления (до 10)\n"
                    "/setprice N — установить лимит цены, например /setprice 900\n"
                    "/status — показать текущие настройки\n"
                    "/debug — показать, сколько объявлений удалось распарсить\n"
                    "/help — список команд"
                )
            elif text.lower().startswith("/status"):
                send_message(f"⚙️ Настройки:\nMAX_PRICE: €{runtime_max_price}\nCHECK_INTERVAL: {CHECK_INTERVAL} сек\nCMD_POLL_SEC: {CMD_POLL_SEC} сек")
            elif text.lower().startswith("/setprice"):
                m = re.search(r"/setprice\s+(\d+)", text.lower())
                if m:
                    runtime_max_price = int(m.group(1))
                    send_message(f"✅ Лимит цены обновлён: теперь ≤ €{runtime_max_price}")
                else:
                    send_message("Укажите число: например, /setprice 900")
            elif text.lower().startswith("/debug"):
                offers_all = fetch_offers(10**9)
                offers_filtered = [o for o in offers_all if o["price"] <= runtime_max_price]
                send_message(f"🔎 DEBUG:\nНайдено всего: {len(offers_all)}\nПодходит по цене ≤€{runtime_max_price}: {len(offers_filtered)}")
            elif text.lower().startswith("/all"):
                data = fetch_offers(runtime_max_price)
                if not data:
                    send_message("⚠️ Сейчас нет объявлений по вашим фильтрам.")
                else:
                    for o in data[:10]:
                        send_message(f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}")
    except Exception as e:
        log("handle_updates error:", e)

def commands_loop():
    send_message(f"🔔 WG-бот запущен (порог ≤ €{runtime_max_price}). Напишите /help для команд.")
    while True:
        handle_updates()
        time.sleep(CMD_POLL_SEC)

def offers_loop():
    while True:
        push_new_offers()
        time.sleep(CHECK_INTERVAL)

# start threads
threading.Thread(target=commands_loop, daemon=True).start()
threading.Thread(target=offers_loop, daemon=True).start()
