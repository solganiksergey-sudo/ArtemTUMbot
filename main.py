import os, re, time, json, requests
from bs4 import BeautifulSoup

# --- Telegram bot setup ---
BOT_TOKEN   = os.environ["BOT_TOKEN"]
TG_API      = f"https://api.telegram.org/bot{BOT_TOKEN}"

# КУДА слать результаты: список ID через запятую (личные, друзья, группы)
# Пример: TARGET_IDS="123456789,-1001234567890,987654321"
TARGET_IDS  = [s.strip() for s in os.environ.get("TARGET_IDS","").split(",") if s.strip()]

# (на всякий) старые переменные тоже поддержим
CHAT_ID     = os.environ.get("CHAT_ID")
if CHAT_ID and CHAT_ID not in TARGET_IDS:
    TARGET_IDS.append(CHAT_ID)

# --- Filters ---
MAX_PRICE       = int(os.environ.get("MAX_PRICE", "800"))
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "600"))  # секунд

# --- Коммьют-фильтр по станциям U6 (15–20 мин до Garching-FZ)
DEFAULT_STATIONS = "Studentenstadt|Freimann|Kieferngarten|Fröttmaning|Froetttmaning|Garching[- ]Hochbrück|Garching Hochbrueck|Garching(?!-Forschungszentrum)"
STATION_PATTERN = os.environ.get("STATIONS", DEFAULT_STATIONS)
station_regex = re.compile(STATION_PATTERN, flags=re.I)

# --- Helpers ---
def log(*a): print(*a, flush=True)

def send_message(text: str, to_id=None, reply_markup=None):
    """Отправка сообщения в один или во все TARGET_IDS."""
    targets = [to_id] if to_id else TARGET_IDS
    for tid in targets:
        try:
            data = {"chat_id": tid, "text": text, "parse_mode": "HTML"}
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
            requests.post(f"{TG_API}/sendMessage", data=data, timeout=20)
        except Exception as e:
            log("send_message error:", e)

def send_message_with_button(text: str, url: str):
    markup = {
        "inline_keyboard": [[{"text": "✉️ Откликнуться", "url": url}],
                            [{"text": "🔗 Открыть объявление", "url": url}]]
    }
    send_message(text, reply_markup=markup)

# --- WG-Gesucht parser ---
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
            if not (ttl_el and price_el and link_el):
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
            # фильтры
            if price > max_price:
                continue
            text_for_match = f"{title} {url}"
            if not station_regex.search(text_for_match):
                continue
            offers.append({"id": oid, "title": title, "price": price, "url": url})
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
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_ids), f)
    except Exception as e:
        log("save_seen error:", e)

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

# --- Commands via getUpdates ---
OFFSET = None
runtime_max_price = MAX_PRICE

def handle_updates():
    global OFFSET, runtime_max_price
    try:
        resp = requests.get(f"{TG_API}/getUpdates",
                            params={"timeout": 10, "offset": OFFSET},
                            timeout=20).json()
    except Exception as e:
        log("getUpdates error", e)
        return
    for u in resp.get("result", []):
        OFFSET = u["update_id"] + 1
        msg = u.get("message")
        if not msg: 
            continue
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id"))
        text = (msg.get("text") or "").strip()

        # Разрешаем команды из любых чатов, где присутствует бот (ЛС/группа)
        # В группе команды лучше писать как /all@ArtemTUMbot, если privacy включён.
        if text.lower().startswith("/help"):
            send_message(
                "Команды:\n"
                "/all – показать все (до 10)\n"
                "/setprice N – задать потолок\n"
                "/status – статус\n"
                "/id – показать chat_id (для ЛС или группы)",
                to_id=chat_id
            )
        elif text.lower().startswith("/id"):
            chat_type = chat.get("type")
            send_message(f"👤 chat_id: {chat_id}\n📦 chat_type: {chat_type}", to_id=chat_id)
        elif text.lower().startswith("/setprice"):
            parts = text.split()
            if len(parts) >= 2 and parts[1].isdigit():
                runtime_max_price = int(parts[1])
                send_message(f"✅ MAX_PRICE обновлён: {runtime_max_price} €", to_id=chat_id)
            else:
                send_message("Ошибка: укажите число после /setprice", to_id=chat_id)
        elif text.lower().startswith("/status"):
            send_message(
                f"⚙️ Настройки:\n"
                f"MAX_PRICE: €{runtime_max_price}\n"
                f"CHECK_INTERVAL: {CHECK_INTERVAL} сек\n"
                f"Фильтр станций: {STATION_PATTERN}\n"
                f"Куда шлём: {', '.join(TARGET_IDS) if TARGET_IDS else 'не задано'}",
                to_id=chat_id
            )
        elif text.lower().startswith("/all"):
            offers = fetch_offers(runtime_max_price)
            if not offers:
                send_message("⚠️ Нет объявлений по фильтрам.", to_id=chat_id)
            for o in offers[:10]:
                msg = f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}"
                # Отправим в тот чат, откуда пришла команда, и всем целям по умолчанию
                send_message_with_button(msg, o["url"])

# --- Main loop ---
send_message(f"🔔 WG-бот запущен. Порог ≤ €{MAX_PRICE}. Фильтр U6 активен.")
while True:
    try:
        handle_updates()
        push_new_offers(runtime_max_price)
    except Exception as e:
        log("loop error:", e)
    time.sleep(CHECK_INTERVAL)
