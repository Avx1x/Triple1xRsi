import os
import time
import json
import argparse
import logging
from datetime import datetime, timezone

import requests

# Optional .env support (for local/Replit development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BINANCE_BASE = "https://api.binance.com"
STATE_FILE = "state.json"


# =========================
# Utility
# =========================

def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def parse_symbols(symbols_str):
    return [s.strip().upper() for s in symbols_str.split(",") if s.strip()]


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# =========================
# RSI Calculation
# =========================

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# =========================
# Data Fetch
# =========================

def fetch_klines(symbol, interval, limit=200):
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def extract_closed_closes(klines):
    now_ms = int(time.time() * 1000)
    closes = []
    times = []

    for k in klines:
        close_time = int(k[6])
        if close_time <= now_ms:
            closes.append(float(k[4]))
            times.append(close_time)

    return closes, times


# =========================
# Alerts
# =========================

def send_telegram(message):
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    r = requests.post(url, json=payload, timeout=15)

    if r.status_code != 200:
        logging.error("Telegram error: %s", r.text)


def send_whatsapp(message):
    phone = env("CALLMEBOT_PHONE")
    apikey = env("CALLMEBOT_APIKEY")

    if not phone or not apikey:
        return

    url = "https://api.callmebot.com/whatsapp.php"
    params = {
        "phone": phone,
        "text": message,
        "apikey": apikey,
        "source": "rsi-bot"
    }

    r = requests.get(url, params=params, timeout=15)

    if r.status_code != 200:
        logging.error("WhatsApp error: %s", r.text)


def send_alert(message):
    logging.info("ALERT: %s", message)
    send_telegram(message)
    send_whatsapp(message)


# =========================
# Core Logic
# =========================

def check_symbol(symbol, interval, period, oversold, overbought, state):
    klines = fetch_klines(symbol, interval)
    closes, times = extract_closed_closes(klines)

    if len(closes) < period + 2:
        return

    current_rsi = compute_rsi(closes, period)
    previous_rsi = compute_rsi(closes[:-1], period)

    last_close_time = times[-1]
    last_price = closes[-1]
    readable_time = datetime.fromtimestamp(
        last_close_time / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    symbol_state = state.setdefault(symbol, {})

    # Cross into oversold
    if previous_rsi > oversold and current_rsi <= oversold:
        if symbol_state.get("last_oversold") != last_close_time:
            msg = (
                f"{symbol} ({interval}) RSI {current_rsi:.2f} → OVERSOLD\n"
                f"Price: {last_price}\n"
                f"Time: {readable_time}"
            )
            send_alert(msg)
            symbol_state["last_oversold"] = last_close_time

    # Cross into overbought
    if previous_rsi < overbought and current_rsi >= overbought:
        if symbol_state.get("last_overbought") != last_close_time:
            msg = (
                f"{symbol} ({interval}) RSI {current_rsi:.2f} → OVERBOUGHT\n"
                f"Price: {last_price}\n"
                f"Time: {readable_time}"
            )
            send_alert(msg)
            symbol_state["last_overbought"] = last_close_time


def main(loop=True):
    symbols = parse_symbols(env("SYMBOLS", "BTCUSDT"))
    interval = env("INTERVAL", "15m")
    period = int(env("RSI_PERIOD", 14))
    oversold = float(env("RSI_OVERSOLD", 30))
    overbought = float(env("RSI_OVERBOUGHT", 70))
    delay = int(env("CHECK_EVERY_SECONDS", 60))

    state = load_state()

    while True:
        try:
            for symbol in symbols:
                check_symbol(symbol, interval, period, oversold, overbought, state)

            save_state(state)

        except Exception as e:
            logging.error("Error: %s", str(e))

        if not loop:
            break

        time.sleep(delay)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    main(loop=not args.once)
