# WG-Gesucht Telegram Bot (Render)
1) Set env vars: BOT_TOKEN, CHAT_ID, (optional) CHECK_INTERVAL, MAX_PRICE.
2) Build: pip install -r requirements.txt
3) Start: gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120
