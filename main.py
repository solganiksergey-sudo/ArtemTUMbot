import os
import time
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask

# ── Конфиг из переменных окружения ─────────────────────────────────────────────
BOT_TOKEN       = os.environ["BOT_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
DEFAULT_MAX     = int(os.environ.get("MAX_PRICE", "800"))
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))

# Глобальный лимит цены (меняется командой /setprice)
runtime_max_price = DEFAULT_MAX

# Ссылки поиска
SEARCH_URLS = [
    "https://www.wg-gesucht.de/1-zimmer-wohnungen-in-Muenchen.90.1.1.0.html",
    "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html"
]

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Память
seen_ids = set()
last_update_id = 0  # чтобы не обрабатывать старые апдейты

# ── Flask (healthcheck для Render) ─────────────────────────────────────────────
app = Flask(__name__)
@app.route("/")
def home(): return "WG bot is running"
@app.route("/health")
def health(): return "ok"

# ── Вспомогательные функции ───────────────────────────────────────────────────
def send_message(text: str):
    try:
        requests.post(f"{TG_API}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
                      timeout=15)
    except Exception as e:
        print("send_message error:", e)

def fetch_offers(max_price: int):
    """Парсим WG-Gesucht и возвращаем список объявлений [{id,title,price,url}]"""
    offers = []
    for url in SEARCH_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Новая логика: объявления как article с data-id
            for ad in soup.find_all("article", attrs={"data-id": True}):
                ad_id = ad.get("data-id")
                title_tag = ad.find("h3")
                price_tag = ad.find(string=re.compile(r"€"))
                link_tag = ad.find("a", href=True)

                if not ad_id or not title_tag or not price_tag or not link_tag:
                    continue

                # Цена: ищем первую цифру
                m = re.search(r"(\d+)", price_tag)
                price = int(m.group(1)) if m else 10**9

                if price <= max_price:
                    offers.append({
                        "id": ad_id,
                        "title": title_tag.get_text(strip=True),
                        "price": price,
                        "url": "https://www.wg-gesucht.de" + link_tag["href"]
                    })
        except Exception as e:
            print("fetch_offers error:", e)
    return offers

def push_new_offers():
    """Шлёт новые (ещё не виденные) объявления"""
    global seen_ids
    offers = fetch_offers(runtime_max_price)
    sent = 0
    for o in offers:
        if o["id"] in seen_ids:
            continue
        seen_ids.add(o["id"])
        msg = f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}"
        send_message(msg)
        sent += 1
    return sent

def handle_updates():
    """Обрабатываем команды /all, /help, /status, /setprice"""
    global last_update_id, runtime_max_price
    try:
        params = {}
        if last_update_id:
            params["offset"] = last_update_id + 1
        resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=15).json()

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
                    "/help — список команд"
                )
            elif text.lower().startswith("/status"):
                send_message(
                    f"⚙️ Настройки:\n"
                    f"MAX_PRICE (runtime): €{runtime_max_price}\n"
                    f"CHECK_INTERVAL (поиск объявлений): {CHECK_INTERVAL} сек"
                )
            elif text.lower().startswith("/setprice"):
                m = re.search(r"/setprice\s+(\d+)", text.lower())
                if m:
                    runtime_max_price = int(m.group(1))
                    send_message(f"✅ Лимит цены обновлён: теперь ≤ €{runtime_max_price}")
                else:
                    send_message("Укажите число: например, /setprice 900")
            elif text.lower().startswith("/all"):
                data = fetch_offers(runtime_max_price)
                if not data:
                    send_message("⚠️ Сейчас нет объявлений по вашим фильтрам.")
                else:
                    for o in data[:10]:
                        send_message(f"🏠 <b>{o['title']}</b>\n💰 {o['price']} €\n🔗 {o['url']}")
    except Exception as e:
        print("handle_updates error:", e)

# ── Циклы ─────────────────────────────────────────────────────────────────────
def commands_loop():
    """Проверяем команды каждые 3 сек"""
    send_message(f"🔔 WG-бот запущен (порог ≤ €{runtime_max_price}). Напишите /help для команд.")
    while True:
        handle_updates()
        time.sleep(3)

def offers_loop():
    """Проверяем новые объявления раз в CHECK_INTERVAL"""
    while True:
        push_new_offers()
        time.sleep(CHECK_INTERVAL)

# ── Запуск ────────────────────────────────────────────────────────────────────
import threading
threading.Thread(target=commands_loop, daemon=True).start()
threading.Thread(target=offers_loop, daemon=True).start()
