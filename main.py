import os, re, time, json, requests
from bs4 import BeautifulSoup
from datetime import datetime

BOT_TOKEN   = os.environ["BOT_TOKEN"]
TG_API      = f"https://api.telegram.org/bot{BOT_TOKEN}"
CHAT_ID     = os.environ.get("CHAT_ID")

MAX_PRICE   = int(os.environ.get("MAX_PRICE", "800"))
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "900"))  # 15 мин между проверками
MIN_TIME    = 15   # мин
MAX_TIME    = 20   # мин

# Google Maps API
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
DESTINATION = "TUM Physics Department, Garching Forschungszentrum, Munich"

# сегодняшняя дата (для фильтрации)
TODAY = datetime.now().strftime("%d.%m.%Y")

def log(*a): print(*a, flush=True)

def send_message(text: str, to_id=None):
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            data={"chat_id": to_id or CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20
        )
    except Exception as e:
        log("send_message error:", e)

BASE_URL = "https://www.wg-gesucht.de/1-zimmer-wohnungen-in-Muenchen.90.1.1.0.html"

def fetch_offers():
    r = requests.get(BASE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(r.text, "html.parser")
    offers = []
    for o in soup.select(".offer_list_item"):
        try:
            ttl_el = o.select_one(".truncate_title")
            price_el = o.select_one(".col-xs-3 b")
            link_el = o.select_one("a[href]")
            date_el = o.select_one(".angabe_klein")

            if not (ttl_el and price_el and link_el and date_el):
                continue

            title = ttl_el.get_text(strip=True)
            price_txt = price_el.get_text(strip=True)
            price_m = re.search(r"\d+", price_txt)
            if not price_m:
                continue
            price = int(price_m.group())
            if price > MAX_PRICE:
                continue

            date_txt = date_el.get_text(strip=True)
            if TODAY not in date_txt:
                continue

            url = "https://www.wg-gesucht.de" + link_el["href"]
            oid_m = re.search(r"\d+", url)
            if not oid_m:
                continue
            oid = oid_m.group()

            # адрес внутри объявления
            addr = fetch_address(url)
            if not addr:
                continue

            # время в пути
            minutes = get_travel_time(addr)
            if not minutes:
                continue
            if minutes < MIN_TIME or minutes > MAX_TIME:
                continue

            offers.append({
                "id": oid,
                "title": title,
                "price": price,
                "url": url,
                "address": addr,
                "minutes": minutes,
                "date": date_txt
            })
        except Exception as e:
            log("parse error:", e)
    return offers

def fetch_address(url):
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        addr_el = soup.select_one(".col-sm-4 .mb-2")
        if addr_el:
            return addr_el.get_text(strip=True)
    except Exception as e:
        log("addr error:", e)
    return None

def get_travel_time(address):
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params={
                "origins": address,
                "destinations": DESTINATION,
                "mode": "transit",
                "key": GOOGLE_KEY
            },
            timeout=30
        ).json()
        rows = resp.get("rows", [])
        if not rows:
            return None
        elements = rows[0].get("elements", [])
        if not elements:
            return None
        dur = elements[0].get("duration", {}).get("value")  # секунды
        if dur:
            return dur // 60
    except Exception as e:
        log("gmap error:", e)
    return None

# --- Storage ---
SEEN_FILE = "seen.json"
try:
    with open(SEEN_FILE) as f:
        seen_ids = set(json.load(f))
except:
    seen_ids = set()

def save_seen():
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_ids), f)
    except Exception as e:
        log("save_seen error:", e)

def push_new_offers():
    offers = fetch_offers()
    new = [o for o in offers if o["id"] not in seen_ids]
    for o in new:
        seen_ids.add(o["id"])
        msg = (
            f"🏠 <b>{o['title']}</b>\n"
            f"💰 {o['price']} €\n"
            f"📅 {o['date']}\n"
            f"📍 {o['address']}\n"
            f"🚌 {o['minutes']} мин до кампуса\n"
            f"🔗 {o['url']}"
        )
        send_message(msg)
    if new:
        save_seen()
    return len(new)

send_message(f"🔔 WG-бот запущен. Фильтры: ≤ €{MAX_PRICE}, {MIN_TIME}-{MAX_TIME} мин до кампуса, дата {TODAY}.")

while True:
    try:
        push_new_offers()
    except Exception as e:
        log("loop error:", e)
    time.sleep(CHECK_INTERVAL)
