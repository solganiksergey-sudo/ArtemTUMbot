import os, re, time, json, requests
from bs4 import BeautifulSoup
from datetime import datetime

BOT_TOKEN   = os.environ["BOT_TOKEN"]
TG_API      = f"https://api.telegram.org/bot{BOT_TOKEN}"

CHAT_ID     = os.environ.get("CHAT_ID")
MAX_PRICE   = int(os.environ.get("MAX_PRICE", "800"))
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "600"))  # секунд

# сегодняшняя дата (чтобы фильтровать по дате добавления)
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

def fetch_offers(max_price=MAX_PRICE):
    r = requests.get(BASE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(r.text, "html.parser")
    offers = []
    for o in soup.select(".offer_list_item"):
        try:
            ttl_el = o.select_one(".truncate_title")
            price_el = o.select_one(".col-xs-3 b")
            link_el = o.select_one("a[href]")
            date_el = o.select_one(".angabe_klein")  # дата публикации

            if not (ttl_el and price_el and link_el and date_el):
                continue

            title = ttl_el.get_text(strip=True)
            price_txt = price_el.get_text(strip=True)
            price_m = re.search(r"\d+", price_txt)
            if not price_m:
                continue
            price = int(price_m.group())
            url = "https://www.wg-gesucht.de" + link_el["href"]
            oid_m = re.search(r"\d+", url)
            if not oid_m:
                continue
            oid = oid_m.group()
            if price > max_price:
                continue

            # фильтр по дате: показываем только если объявление от сегодняшнего дня
            date_txt = date_el.get_text(strip=True)
            if TODAY not in date_txt:
                continue

            offers.append({"id": oid, "title": title, "price": price, "url": url, "date": date_txt})
        except Exception as e:
            log("parse error:", e)
    return offers

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

def push_new_offers(max_price=MAX_PRICE):
    offers = fetch_offers(max_price)
    new = [o for o in offers if o["id"] not in seen_ids]
    for o in new:
        seen_ids.add(o["id"])
        msg = f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n📅 {o['date']}\n🔗 {o['url']}"
        send_message(msg)
    if new:
        save_seen()
    return len(new)

send_message(f"🔔 WG-бот запущен (порог ≤ €{MAX_PRICE}). Показываю только новые варианты за {TODAY}.")

while True:
    try:
        push_new_offers(MAX_PRICE)
    except Exception as e:
        log("loop error:", e)
    time.sleep(CHECK_INTERVAL)
