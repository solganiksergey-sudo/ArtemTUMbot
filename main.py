import os, time, re, threading, random
import requests
from bs4 import BeautifulSoup
from flask import Flask

# === Config via environment variables ===
BOT_TOKEN   = os.environ["BOT_TOKEN"]
CHAT_ID     = os.environ["CHAT_ID"]
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
MAX_PRICE      = int(os.environ.get("MAX_PRICE", "800"))

SEARCH_URLS = {
    "WG-Zimmer": "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html?offer_filter=1&city_id=90&category=0&rent_type=0&ot%5B%5D=147&ot%5B%5D=148&ot%5B%5D=149&ot%5B%5D=102&ot%5B%5D=100&ot%5B%5D=95&from=01.09.2025&to=01.08.2026",
    "1-Zimmer-Wohnung": "https://www.wg-gesucht.de/1-zimmer-wohnungen-in-Muenchen.90.1.1.0.html?offer_filter=1&city_id=90&category=1&rent_type=0&ot%5B%5D=147&ot%5B%5D=148&ot%5B%5D=149&ot%5B%5D=102&ot%5B%5D=100&ot%5B%5D=95&from=01.09.2025&to=01.08.2026"
}

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
seen_ids = set()

def send_msg(text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print("send_msg error:", e)

def send_photo(photo_url: str, caption: str):
    try:
        requests.post(f"{TG_API}/sendPhoto", data={"chat_id": CHAT_ID, "caption": caption, "photo": photo_url}, timeout=30)
    except Exception as e:
        print("send_photo error:", e); send_msg(caption)

def fetch_offers(url, category):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    offers = []
    for div in soup.find_all("div", {"class": "offer_list_item"}):
        offer_id = div.get("adid") or div.get("id") or str(hash(div))
        title_tag = div.find("h3")
        link_tag = div.find("a", href=True)

        price = None
        price_tag = div.find("div", class_=re.compile("col-xs-3"))
        if price_tag:
            m = re.search(r"(\d+)", price_tag.get_text(strip=True))
            if m: price = int(m.group(1))

        size = None
        size_tag = div.find("div", class_=re.compile("col-xs-2"))
        if size_tag:
            m = re.search(r"(\d+)", size_tag.get_text(strip=True))
            if m: size = m.group(1) + " m²"

        move_in = None
        move_in_tag = div.find("div", class_=re.compile("col-xs-5"))
        if move_in_tag:
            move_in = move_in_tag.get_text(strip=True)

        img_url = None
        img_tag = div.find("img")
        if img_tag and img_tag.get("src"):
            img_url = img_tag["src"]
            if img_url.startswith("/"):
                img_url = "https://www.wg-gesucht.de" + img_url

        if title_tag and link_tag and price is not None and price <= MAX_PRICE:
            title = title_tag.get_text(strip=True)
            link = "https://www.wg-gesucht.de" + link_tag["href"]
            offers.append((offer_id, title, link, price, size, move_in, img_url, category))
    return offers

def worker_loop():
    send_msg(f"🔔 WG-бот запущен на Render (≤ €{MAX_PRICE}).")
    while True:
        try:
            for category, url in SEARCH_URLS.items():
                for (offer_id, title, link, price, size, move_in, img_url, cat) in fetch_offers(url, category):
                    if offer_id not in seen_ids:
                        seen_ids.add(offer_id)
                        caption = (f"🏠 Новое объявление ({cat}):\n"
                                   f"{title}\n"
                                   f"💰 Цена: {price} €\n"
                                   f"📐 Площадь: {size or 'не указано'}\n"
                                   f"📅 Въезд: {move_in or 'не указано'}\n"
                                   f"🔗 {link}")
                        if img_url: send_photo(img_url, caption)
                        else: send_msg(caption)
        except Exception as e:
            print("loop error:", e)
        time.sleep(CHECK_INTERVAL + random.randint(0, 60))

from flask import Flask
app = Flask(__name__)

@app.get("/")
def index(): return "WG bot is running"

@app.get("/health")
def health(): return "ok"

import threading
threading.Thread(target=worker_loop, daemon=True).start()
