#!/usr/bin/env python3
"""
Crypto Signal Monitor — watch-only alert bot
----------------------------------------------
Monitorea BTC y ETH en Binance, calcula RSI, cruce de medias móviles y
momentum sobre velas CERRADAS (evita repintado), y manda una alerta por
Telegram cuando 2+ señales se alinean, con volumen y tendencia mayor
como filtros obligatorios.

NO ejecuta órdenes. NO es consejo financiero. Solo vigila y avisa.
"""

import os
import json
import sys
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SYMBOLS = os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",")
INTERVAL = os.environ.get("INTERVAL", "15m")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", 14))
MA_FAST = int(os.environ.get("MA_FAST", 9))
MA_SLOW = int(os.environ.get("MA_SLOW", 21))
RSI_LOW = float(os.environ.get("RSI_LOW", 30))
RSI_HIGH = float(os.environ.get("RSI_HIGH", 70))
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
KLINES_LIMIT = 150

HTF_INTERVAL = os.environ.get("HTF_INTERVAL", "4h")
HTF_SMA_PERIOD = int(os.environ.get("HTF_SMA_PERIOD", 50))
REQUIRE_VOLUME = os.environ.get("REQUIRE_VOLUME", "true").lower() == "true"
REQUIRE_HTF_TREND = os.environ.get("REQUIRE_HTF_TREND", "true").lower() == "true"

BINANCE_URL = "https://api.binance.com/api/v3/klines"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def fetch_klines(symbol, interval, limit=KLINES_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit + 1}
    r = requests.get(BINANCE_URL, params=params, timeout=15)
    r.raise_for_status()
    raw = r.json()
    candles = [
        {
            "time": k[0], "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            "is_closed": True,
        }
        for k in raw
    ]
    if candles:
        candles[-1]["is_closed"] = False
    return candles


def sma(values, period):
    out = [None] * len(values)
    for i in range(len(values)):
        if i < period - 1:
            continue
        out[i] = sum(values[i - period + 1: i + 1]) / period
    return out


def rsi(values, period):
    out = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0
        loss = -diff if diff < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
    return out


def last_valid(arr):
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def cross_state(fast_arr, slow_arr):
    n = len(fast_arr)
    i_last = -1
    for i in range(n - 1, 0, -1):
        if None not in (fast_arr[i], slow_arr[i], fast_arr[i - 1], slow_arr[i - 1]):
            i_last = i
            break
    if i_last < 1:
        return "neutral"
    prev_diff = fast_arr[i_last - 1] - slow_arr[i_last - 1]
    cur_diff = fast_arr[i_last] - slow_arr[i_last]
    if prev_diff <= 0 and cur_diff > 0:
        return "bull_cross"
    if prev_diff >= 0 and cur_diff < 0:
        return "bear_cross"
    return "above" if cur_diff > 0 else "below"


def momentum(closes, lookback):
    n = len(closes)
    if n < lookback + 1:
        return 0.0
    past, now = closes[n - 1 - lookback], closes[n - 1]
    return ((now - past) / past) * 100


def higher_timeframe_trend(symbol):
    try:
        candles = fetch_klines(symbol, HTF_INTERVAL, limit=HTF_SMA_PERIOD + 5)
    except Exception as e:
        print(f"[WARN] {symbol}: no se pudo obtener tendencia {HTF_INTERVAL} ({e})")
        return None
    closed = [c for c in candles if c["is_closed"]]
    closes = [c["close"] for c in closed]
    if len(closes) < HTF_SMA_PERIOD:
        return None
    ma = sma(closes, HTF_SMA_PERIOD)
    ma_now = last_valid(ma)
    if ma_now is None:
        return None
    last_close = closes[-1]
    if last_close > ma_now:
        return "bull"
    if last_close < ma_now:
        return "bear"
    return None


def volume_confirmation(volumes, lookback=20, multiplier=1.2):
    if len(volumes) < lookback + 1:
        return False, 0.0
    recent = volumes[-1]
    avg = sum(volumes[-(lookback + 1):-1]) / lookback
    if avg == 0:
        return False, 0.0
    ratio = recent / avg
    return ratio >= multiplier, ratio


def evaluate(symbol, candles):
    live_price = candles[-1]["close"]
    closed = [c for c in candles if c["is_closed"]]
    closes = [c["close"] for c in closed]
    volumes = [c["volume"] for c in closed]

    rsi_arr = rsi(closes, RSI_PERIOD)
    rsi_now = last_valid(rsi_arr)
    fast_arr = sma(closes, MA_FAST)
    slow_arr = sma(closes, MA_SLOW)
    cross = cross_state(fast_arr, slow_arr)
    mom = momentum(closes, min(10, len(closes) - 1))
    vol_confirmed, vol_ratio = volume_confirmation(volumes)

    rsi_kind = "neutral"
    if rsi_now is not None:
        if rsi_now <= RSI_LOW:
            rsi_kind = "bull"
        elif rsi_now >= RSI_HIGH:
            rsi_kind = "bear"

    cross_kind = "neutral"
    if cross == "bull_cross":
        cross_kind = "bull"
    elif cross == "bear_cross":
        cross_kind = "bear"

    mom_kind = "neutral"
    if mom > 1.2:
        mom_kind = "bull"
    elif mom < -1.2:
        mom_kind = "bear"

    bull = [rsi_kind, cross_kind, mom_kind].count("bull")
    bear = [rsi_kind, cross_kind, mom_kind].count("bear")

    base_direction = None
    if bull >= 2:
        base_direction = "bull"
    elif bear >= 2:
        base_direction = "bear"

    last_candle = closed[-1] if closed else None
    candle_direction = None
    if last_candle:
        if last_candle["close"] > last_candle["open"]:
            candle_direction = "bull"
        elif last_candle["close"] < last_candle["open"]:
            candle_direction = "bear"

    htf_trend = higher_timeframe_trend(symbol)

    direction = base_direction
    gate_reasons = []

    if direction and REQUIRE_VOLUME:
        volume_ok = vol_confirmed and candle_direction == direction
        if not volume_ok:
            gate_reasons.append("volumen no confirma")
            direction = None

    if direction and REQUIRE_HTF_TREND:
        if htf_trend is None or htf_trend != base_direction:
            gate_reasons.append(f"tendencia {HTF_INTERVAL} no alineada")
            direction = None

    return {
        "symbol": symbol, "price": live_price,
        "closed_close": closes[-1] if closes else live_price,
        "rsi": rsi_now, "cross": cross, "momentum": mom,
        "volume_confirmed": vol_confirmed, "volume_ratio": vol_ratio,
        "base_direction": base_direction, "htf_trend": htf_trend,
        "direction": direction, "blocked_by": gate_reasons,
        "last_closed_candle_time": closed[-1]["time"] if closed else None,
    }


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID — no se envía alerta.")
        print(text)
        return
    url = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    r = requests.post(url, data=payload, timeout=15)
    if not r.ok:
        print(f"[ERROR] Telegram respondió {r.status_code}: {r.text}")


def format_alert(result):
    label = result["symbol"].replace("USDT", "")
    icon = "🟢" if result["direction"] == "bull" else "🔴"
    tag = "posible rebote (señales alcistas alineadas)" if result["direction"] == "bull" \
        else "posible agotamiento (señales bajistas alineadas)"
    rsi_txt = f"{result['rsi']:.1f}" if result["rsi"] is not None else "—"
    vol_txt = f"{result['volume_ratio']:.2f}x" + (" ✅ confirma" if result["volume_confirmed"] else " (débil)")
    return (
        f"{icon} *{label}* — {tag}\n"
        f"Precio: ${result['price']:,.2f}\n"
        f"RSI({RSI_PERIOD}): {rsi_txt}\n"
        f"Medias {MA_FAST}/{MA_SLOW}: {result['cross']}\n"
        f"Momentum: {result['momentum']:+.2f}%\n"
        f"Volumen vs. promedio: {vol_txt}\n"
        f"Tendencia {HTF_INTERVAL}: {result['htf_trend']} (alineada) ✅\n"
        f"Timeframe: {INTERVAL} (vela cerrada)\n\n"
        f"_Watch-only. Vos decidís qué hacer con esto._"
    )


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    state = load_state()
    any_sent = False

    for symbol in SYMBOLS:
        symbol = symbol.strip()
        try:
            candles = fetch_klines(symbol, INTERVAL)
        except Exception as e:
            print(f"[ERROR] {symbol}: no se pudo obtener datos ({e})")
            continue

        if len([c for c in candles if c["is_closed"]]) < max(RSI_PERIOD, MA_SLOW) + 1:
            print(f"[WARN] {symbol}: no hay suficientes velas cerradas todavía, se salta este ciclo.")
            continue

        result = evaluate(symbol, candles)

        symbol_state = state.get(symbol, {})
        prev_direction = symbol_state.get("direction")
        prev_candle_time = symbol_state.get("candle_time")
        same_candle = prev_candle_time == result["last_closed_candle_time"]

        should_alert = (
            result["direction"]
            and not (result["direction"] == prev_direction and same_candle)
        )

        if should_alert:
            send_telegram(format_alert(result))
            any_sent = True

        state[symbol] = {
            "direction": result["direction"],
            "candle_time": result["last_closed_candle_time"],
        }

        print(
            f"{symbol}: price={result['price']:.2f} rsi={result['rsi']} "
            f"cross={result['cross']} mom={result['momentum']:.2f}% "
            f"vol_ratio={result['volume_ratio']:.2f} "
            f"base={result['base_direction']} htf_trend={result['htf_trend']} "
            f"final={result['direction']} "
            f"blocked_by={result['blocked_by'] or '-'}"
        )

    save_state(state)
    if not any_sent:
        print("Sin señales nuevas en este ciclo.")


if __name__ == "__main__":
    sys.exit(main())
