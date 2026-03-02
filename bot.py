

import os
import time
import threading
from datetime import datetime, timezone

import ccxt
import pandas as pd
from flask import Flask

# ===================== SETTINGS =====================
API_KEY = os.getenv("MEXC_API_KEY", "mx0vglYklOGQGI9761")
API_SECRET = os.getenv("MEXC_API_SECRET", "529c8bbba99e4b3785d30a8bc5da4594")
SYMBOL = os.getenv("SYMBOL", "SOL/USDT")

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

TP = float(os.getenv("TP_PCT", "0.007"))  # 0.7%
SL = float(os.getenv("SL_PCT", "0.004"))  # 0.4%

TICKER_POLL_SEC = int(os.getenv("TICKER_POLL_SEC", "2"))          # fast price refresh
CANDLE_REFRESH_SEC = int(os.getenv("CANDLE_REFRESH_SEC", "20"))   # 5m RSI refresh
TREND_REFRESH_SEC = int(os.getenv("TREND_REFRESH_SEC", "300"))    # 1h trend refresh
PORT = int(os.getenv("PORT", "5000"))

# ===================== EXCHANGE =====================
exchange = ccxt.mexc({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})

# ===================== BOT STATE (shown on dashboard) =====================
state = {
    "status": "STARTING",
    "symbol": SYMBOL,
    "dry_run": DRY_RUN,
    "price": None,
    "trend_ok": None,
    "trend_msg": "",
    "rsi_5m": None,
    "in_position": False,
    "entry": None,
    "tp": None,
    "sl": None,
    "trades_today": 0,
    "losses_today": 0,
    "last_update_utc": None,
    "last_action": "",
    "last_error": "",
}

def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ===================== BOT LOOP =====================
def bot_loop():
    global state

    in_position = False
    entry_price = 0.0

    last_candle_check = 0
    last_trend_check = 0
    trend_ok = False

    state["status"] = "RUNNING"
    state["last_update_utc"] = utc_now_str()
    print("BOT + DASHBOARD STARTED", flush=True)

    while True:
        try:
            # 1) Fast ticker
            t = exchange.fetch_ticker(SYMBOL)
            price = float(t["last"])
            state["price"] = price

            # 2) Manage position first (even if trend turns bearish)
            if in_position:
                tp_price = entry_price * (1 + TP)
                sl_price = entry_price * (1 - SL)

                state["tp"] = tp_price
                state["sl"] = sl_price

                if price >= tp_price:
                    state["last_action"] = f"TP HIT (DRY_RUN={DRY_RUN}) at {price:.4f}"
                    print(state["last_action"], flush=True)
                    in_position = False
                    state["in_position"] = False
                    state["entry"] = None

                elif price <= sl_price:
                    state["last_action"] = f"SL HIT (DRY_RUN={DRY_RUN}) at {price:.4f}"
                    print(state["last_action"], flush=True)
                    state["losses_today"] += 1
                    in_position = False
                    state["in_position"] = False
                    state["entry"] = None

                state["last_update_utc"] = utc_now_str()
                time.sleep(TICKER_POLL_SEC)
                continue

            # 3) Trend check (1H EMA100) every 5 min
            now = time.time()
            if now - last_trend_check >= TREND_REFRESH_SEC:
                ohlcv_1h = exchange.fetch_ohlcv(SYMBOL, timeframe="1h", limit=120)
                df_1h = pd.DataFrame(ohlcv_1h, columns=["ts","o","h","l","c","v"])
                ema100 = df_1h["c"].ewm(span=100).mean().iloc[-1]
                last_1h_close = float(df_1h["c"].iloc[-1])
                trend_ok = last_1h_close >= float(ema100)

                state["trend_ok"] = trend_ok
                state["trend_msg"] = f"1H close={last_1h_close:.4f} ema100={float(ema100):.4f}"
                print("TREND:", state["trend_msg"], "ok=", trend_ok, flush=True)

                last_trend_check = now

            # If trend not ok, do nothing
            if not trend_ok:
                state["last_action"] = "NO TRADE: Trend filter blocked"
                state["last_update_utc"] = utc_now_str()
                time.sleep(TICKER_POLL_SEC)
                continue

            # 4) Setup check (5m RSI) every 20 sec
            if now - last_candle_check >= CANDLE_REFRESH_SEC:
                ohlcv_5m = exchange.fetch_ohlcv(SYMBOL, timeframe="5m", limit=120)
                df_5m = pd.DataFrame(ohlcv_5m, columns=["ts","o","h","l","c","v"])
                df_5m["rsi"] = rsi(df_5m["c"])
                current_rsi = float(df_5m["rsi"].iloc[-1])

                state["rsi_5m"] = current_rsi

                # Entry rule (DRY RUN): RSI dip
                if current_rsi < 35:
                    entry_price = price
                    in_position = True
                    state["in_position"] = True
                    state["entry"] = entry_price
                    state["tp"] = entry_price * (1 + TP)
                    state["sl"] = entry_price * (1 - SL)
                    state["trades_today"] += 1

                    state["last_action"] = f"ENTER (DRY_RUN={DRY_RUN}) at {entry_price:.4f} | RSI={current_rsi:.1f}"
                    print(state["last_action"], flush=True)
                else:
                    state["last_action"] = f"WAIT: RSI={current_rsi:.1f}"

                last_candle_check = now

            state["last_update_utc"] = utc_now_str()
            time.sleep(TICKER_POLL_SEC)

        except Exception as e:
            state["last_error"] = str(e)
            state["last_update_utc"] = utc_now_str()
            print("Error:", e, flush=True)
            time.sleep(10)

# ===================== DASHBOARD =====================
app = Flask(__name__)

@app.get("/")
def home():
    s = state
    html = f"""
    <html>
    <head>
      <meta http-equiv="refresh" content="2">
      <title>Scalp Bot Dashboard</title>
      <style>
        body {{ font-family: Arial; padding: 20px; }}
        .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 16px; max-width: 700px; }}
        .row {{ display: flex; justify-content: space-between; padding: 6px 0; }}
        .k {{ color: #666; }}
        .v {{ font-weight: 600; }}
        .ok {{ color: green; }}
        .bad {{ color: red; }}
        code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 6px; }}
      </style>
    </head>
    <body>
      <h2>Scalp Bot Dashboard</h2>
      <div class="card">
        <div class="row"><div class="k">Status</div><div class="v">{s["status"]}</div></div>
        <div class="row"><div class="k">Symbol</div><div class="v"><code>{s["symbol"]}</code></div></div>
        <div class="row"><div class="k">DRY RUN</div><div class="v">{s["dry_run"]}</div></div>

        <hr/>

        <div class="row"><div class="k">Live Price</div><div class="v">{s["price"]}</div></div>
        <div class="row"><div class="k">Trend OK</div><div class="v">{('<span class="ok">YES</span>' if s["trend_ok"] else '<span class="bad">NO</span>')}</div></div>
        <div class="row"><div class="k">Trend Info</div><div class="v">{s["trend_msg"]}</div></div>
        <div class="row"><div class="k">RSI (5m)</div><div class="v">{s["rsi_5m"]}</div></div>

        <hr/>

        <div class="row"><div class="k">In Position</div><div class="v">{s["in_position"]}</div></div>
        <div class="row"><div class="k">Entry</div><div class="v">{s["entry"]}</div></div>
        <div class="row"><div class="k">TP</div><div class="v">{s["tp"]}</div></div>
        <div class="row"><div class="k">SL</div><div class="v">{s["sl"]}</div></div>

        <hr/>

        <div class="row"><div class="k">Trades Today</div><div class="v">{s["trades_today"]}</div></div>
        <div class="row"><div class="k">Losses Today</div><div class="v">{s["losses_today"]}</div></div>

        <hr/>

        <div class="row"><div class="k">Last Action</div><div class="v">{s["last_action"]}</div></div>
        <div class="row"><div class="k">Last Error</div><div class="v">{s["last_error"]}</div></div>
        <div class="row"><div class="k">Last Update (UTC)</div><div class="v">{s["last_update_utc"]}</div></div>
      </div>
      <p style="color:#777;margin-top:12px;">Auto-refresh every 2 seconds.</p>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    # host 0.0.0.0 is REQUIRED inside Docker
    app.run(host="0.0.0.0", port=PORT)
