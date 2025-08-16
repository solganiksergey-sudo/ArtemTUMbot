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

# Глобальный лимит цены (можно менять командой /setprice)
runtime_max_price = DEFAULT_MAX

# Источники (можете заменить на свои отфильтрованные ссылки WG-Gesucht)
SEARCH_URLS = [
    "https://www.wg-gesucht.de/1-zimmer-wohnungen-in-Muenchen.90.1.1.0.html",
    "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html"
]

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Память
seen_ids = set()
last_update_id = 0  # чтобы не обрабатывать апдейты повторно

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
    """Возвращает список объявлений: [{id,title,price,url}] с ценой <= max_price"""
    offers = []
    for url in SEARCH_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for ad in soup.find_all("div", class_="offer_list_item"):
                ad_id = ad.get("adid")
                if not ad_id:
                    continue
                title_tag = ad.find("h3")
                price_tag = ad.find("div", class_="col-xs-3")
                link_tag = ad.find("a", href=True)
                if not title_tag or not price_tag or not link_tag:
                    continue

                # вытащим первую группу цифр как цену (обычно это warm/kalt цифра)
                m = re.search(r"(\d+)", price_tag.get_text(strip=True))
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
    """Шлёт новые (ещё не виденные) объявления с лимитом runtime_max_price"""
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
    """Обработчик команд /all, /help, /setprice N, /status"""
    global last_update_id, runtime_max_price
    try:
        params = {"timeout": 0}
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
                continue  # игнорируем чужие чаты

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
                    f"⚙️ Текущие настройки:\n"
                    f"MAX_PRICE (runtime): €{runtime_max_price}\n"
                    f"MAX_PRICE (default): €{DEFAULT_MAX}\n"
                    f"CHECK_INTERVAL: {CHECK_INTERVAL} сек"
                )
            elif text.lower().startswith("/setprice"):
                m = re.search(r"/setprice\s+(\d+)", text.lower())
                if m:
                    new_price = int(m.group(1))
                    runtime_max_price = new_price
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

def main_loop():
    send_message(f"🔔 WG-бот запущен (порог ≤ €{runtime_max_price}). Напишите /help для команд.")
    while True:
        try:
            # 1) обработать команды
            handle_updates()
            # 2) проверить новые объявления
            push_new_offers()
        except Exception as e:
            print("main_loop error:", e)
        time.sleep(CHECK_INTERVAL)

# ── Запуск фонового цикла при старте веб-приложения ───────────────────────────
import threading
threading.Thread(target=main_loop, daemon=True).start()
