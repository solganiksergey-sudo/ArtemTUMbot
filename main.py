import os, re, time, json, requests
from bs4 import BeautifulSoup

# --- Telegram bot setup ---
BOT_TOKEN   = os.environ["BOT_TOKEN"]
CHAT_ID     = os.environ["CHAT_ID"]
FORWARD_ID  = os.environ.get("FORWARD_ID")
TG_API      = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- Filters ---
MAX_PRICE       = int(os.environ.get("MAX_PRICE", "800"))
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "600"))  # секунд

# --- Коммьют-фильтр по станциям U6 (15–20 мин до Garching-FZ)
DEFAULT_STATIONS = "Studentenstadt|Freimann|Kieferngarten|Fröttmaning|Froetttmaning|Garching[- ]Hochbrück|Garching Hochbrueck|Garching(?!-Forschungszentrum)"
STATION_PATTERN = os.environ.get("STATIONS", DEFAULT_STATIONS)
station_regex = re.compile(STATION_PATTERN, flags=re.I)

# --- Helpers ---
def log(*a): print(*a, flush=True)

def send_message(text: str):
    try:
        # вам
        requests.post(f"{TG_API}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
                      timeout=20)
        # другу
        if FORWARD_ID:
            requests.post(f"{TG_API}/sendMessage",
                          data={"chat_id": FORWARD_ID, "text": text, "parse_mode": "HTML"},
                          timeout=20)
    except Exception as e:
        log("send_message error:", e)

def send_message_with_button(text: str, url: str):
    markup = {
        "inline_keyboard": [[{"text": "✉️ Откликнуться", "url": url}],
                            [{"text": "🔗 Открыть объявление", "url": url}]]
    }
    try:
        # вам
        requests.post(f"{TG_API}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                            "reply_markup": json.dumps(markup)},
                      timeout=20)
        # другу
        if FORWARD_ID:
            requests.post(f"{TG_API}/sendMessage",
                          data={"chat_id": FORWARD_ID, "text": text, "parse_mode": "HTML",
                                "reply_markup": json.dumps(markup)},
                          timeout=20)
    except Exception as e:
        log("send_message_with_button error:", e)

# --- WG-Gesucht parser ---
BASE_URL = "https://www.wg-gesucht.de/1-zimmer-wohnungen-in-Muenchen.90.1.1.0.html"

def fetch_offers(max_price=MAX_PRICE):
    r = requests.get(BASE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(r.text, "html.parser")
    offers = []
    for o in soup.select(".offer_list_item"):
        try:
            title = o.select_one(".truncate_title").get_text(strip=True)
            price_txt = o.select_one(".col-xs-3 b").get_text(strip=True)
            price = int(re.search(r"\d+", price_txt).group())
            url = "https://www.wg-gesucht.de" + o.select_one("a")["href"]
            oid = re.search(r"\d+", url).group()
            data = {"id": oid, "title": title, "price": price, "url": url}

            # фильтр по цене
            if price > max_price:
                continue

            # фильтр по станциям
            text_for_match = " ".join([title, url])
            if not station_regex.search(text_for_match):
                continue

            offers.append(data)
        except Exception as e:
            log("parse error:", e)
    return offers

# --- Storage ---
SEEN_FILE = "seen.json"
try:
    with open(SEEN_FILE) as f:
        seen_ids = set(json.load(f))
except:
    seen_ids = set()

def save_seen():
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_ids), f)

# --- Push new offers ---
def push_new_offers(max_price=MAX_PRICE):
    offers = fetch_offers(max_price)
    new = [o for o in offers if o["id"] not in seen_ids]
    for o in new:
        seen_ids.add(o["id"])
        msg = f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}"
        send_message_with_button(msg, o["url"])
    if new:
        save_seen()
    return len(new)

# --- Bot loop ---
OFFSET = None
runtime_max_price = MAX_PRICE

def handle_updates():
    global OFFSET, runtime_max_price
    try:
        r = requests.get(f"{TG_API}/getUpdates",
                         params={"timeout": 10, "offset": OFFSET}, timeout=20).json()
    except Exception as e:
        log("getUpdates error", e)
        return
    for u in r.get("result", []):
        OFFSET = u["update_id"] + 1
        msg = u.get("message")
        if not msg: continue
        chat_id = str(msg["chat"]["id"])
        text = msg.get("text","")
        if chat_id != CHAT_ID and chat_id != FORWARD_ID:
            continue
        if text.lower().startswith("/help"):
            send_message("Команды:\n/all – показать все\n/setprice N – задать потолок\n/status – статус")
        elif text.lower().startswith("/all"):
            offers = fetch_offers(runtime_max_price)
            if not offers:
                send_message("⚠️ Нет объявлений по фильтрам.")
            for o in offers[:10]:
                msg = f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}"
                send_message_with_button(msg, o["url"])
        elif text.lower().startswith("/setprice"):
            try:
                runtime_max_price = int(text.split()[1])
                send_message(f"✅ MAX_PRICE обновлён: {runtime_max_price} €")
            except:
                send_message("Ошибка: укажите число после /setprice")
        elif text.lower().startswith("/status"):
            send_message(f"⚙️ Настройки:\nMAX_PRICE: €{runtime_max_price}\n"
                         f"CHECK_INTERVAL: {CHECK_INTERVAL} сек\n"
                         f"Фильтр станций: {STATION_PATTERN}")

# --- Main loop ---
send_message(f"🔔 WG-бот запущен (порог ≤ €{MAX_PRICE}, фильтр U6 активен). Напишите /help для команд.")

while True:
    try:
        handle_updates()
        push_new_offers(runtime_max_price)
    except Exception as e:
        log("loop error:", e)
    time.sleep(CHECK_INTERVAL)
