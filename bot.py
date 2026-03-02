import os
import time
import ccxt

API_KEY = os.getenv("MEXC_API_KEY", "mx0vglYklOGQGI9761")
API_SECRET = os.getenv("MEXC_API_SECRET", "529c8bbba99e4b3785d30a8bc5da4594")
SYMBOL = os.getenv("SYMBOL", "SOL/USDT")

exchange = ccxt.mexc({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})

print("Bot started. Reading price...", flush=True)

while True:
    try:
        t = exchange.fetch_ticker(SYMBOL)
        print("Price:", t["last"], flush=True)
        time.sleep(15)
    except Exception as e:
        print("Error:", e, flush=True)
        time.sleep(30)
