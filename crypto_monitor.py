#!/usr/bin/env python3
"""
Crypto Signal Monitor v3 — dos sistemas separados
----------------------------------------------------
Sistema 1 (Zona de giro, velas de 15m): detecta posibles puntos de
entrada — RSI en extremo + precio en el borde de Bollinger + volumen que
confirma. No exige que la tendencia mayor esté de acuerdo, porque un giro
por definición ocurre en contra de lo que venía pasando.

Sistema 2 (Estado de tendencia, velas de 4h + 1d): reporta si la
tendencia mayor está alcista, bajista o neutral — no es una alerta
puntual, es un estado que se recalcula cada corrida, y avisa por Telegram
solo cuando ese estado CAMBIA (para no repetir lo mismo todo el día).

Las dos alertas de giro se combinan con el estado de tendencia vigente
para etiquetar qué tan fuerte es la señal: si la tendencia mayor confirma
el giro, si es neutral, o si va en contra (más riesgo).

NO ejecuta órdenes. NO es consejo financiero. Solo vigila y avisa.
"""

import os
import json
import sys
import time
import requests

# ---------- Config general ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SYMBOLS = os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
KLINES_LIMIT = 150

# ---------- Sistema 1: Zona de giro (15m) ----------
REVERSAL_INTERVAL = os.environ.get("REVERSAL_INTERVAL", "15m")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", 14))
RSI_LOW = float(os.environ.get("RSI_LOW", 30))
RSI_HIGH = float(os.environ.get("RSI_HIGH", 70))
BB_PERIOD = int(os.environ.get("BB_PERIOD", 20))
BB_STD = float(os.environ.get("BB_STD", 2.0))
VOLUME_LOOKBACK = int(os.environ.get("VOLUME_LOOKBACK", 20))
VOLUME_MULTIPLIER = float(os.environ.get("VOLUME_MULTIPLIER", 1.5))

# ---------- Sistema 2: Estado de tendencia (4h + 1d) ----------
TREND_INTERVAL = os.environ.get("TREND_INTERVAL", "4h")
DAILY_INTERVAL = os.environ.get("DAILY_INTERVAL", "1d")
MA_FAST = int(os.environ.get("MA_FAST", 9))
MA_SLOW = int(os.environ.get("MA_SLOW", 21))
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", 14))
MOMENTUM_LOOKBACK = int(os.environ.get("MOMENTUM_LOOKBACK", 10))
MOMENTUM_ATR_MULTIPLIER = float(os.environ.get("MOMENTUM_ATR_MULTIPLIER", 1.5))
USE_DAILY_CONFIRMATION = os.environ.get("USE_DAILY_CONFIRMATION", "true").lower() == "true"
DAILY_SMA = int(os.environ.get("DAILY_SMA", 50))

# ---------- Paper-testing de las señales de giro ----------
SIGNALS_LOG_FILE = os.environ.get("SIGNALS_LOG_FILE", "signals_log.jsonl")
TARGET_ATR_MULT = float(os.environ.get("TARGET_ATR_MULT", 1.5))
STOP_ATR_MULT = float(os.environ.get("STOP_ATR_MULT", 1.0))
HORIZON_CANDLES = int(os.environ.get("HORIZON_CANDLES", 4))
NOTIFY_ON_RESOLUTION = os.environ.get("NOTIFY_ON_RESOLUTION", "true").lower() == "true"
SUMMARY_INTERVAL_HOURS = float(os.environ.get("SUMMARY_INTERVAL_HOURS", 24))

KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_PAIR_MAP = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD"}
KRAKEN_INTERVAL_MAP = {"15m": 15, "4h": 240, "1d": 1440}
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


# ==================== Datos ====================
def fetch_klines(symbol, interval, limit=KLINES_LIMIT):
    pair = KRAKEN_PAIR_MAP.get(symbol)
    kraken_interval = KRAKEN_INTERVAL_MAP.get(interval)
    if not pair or not kraken_interval:
        raise ValueError(f"Símbolo o intervalo no soportado en Kraken: {symbol} {interval}")

    r = requests.get(KRAKEN_URL, params={"pair": pair, "interval": kraken_interval}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken devolvió error: {data['error']}")

    result = data["result"]
    # La respuesta trae la clave del par (ej. "XXBTZUSD") + "last" al mismo
    # nivel — nos quedamos con la que NO es "last", sin asumir el nombre exacto.
    candles_key = next(k for k in result.keys() if k != "last")
    raw = result[candles_key][-(limit + 1):]

    candles = [
        {
            "time": int(row[0]) * 1000,  # Kraken usa segundos; normalizamos a ms (igual que antes)
            "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]), "volume": float(row[6]),
            "is_closed": True,
        }
        for row in raw
    ]
    if candles:
        candles[-1]["is_closed"] = False  # misma vela en curso, mismo criterio de antes
    return candles


def closed_only(candles):
    return [c for c in candles if c["is_closed"]]


# ==================== Indicadores (compartidos) ====================
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


def bollinger_bands(values, period, num_std):
    mid = sma(values, period)
    upper = [None] * len(values)
    lower = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None:
            continue
        window = values[i - period + 1: i + 1]
        mean = mid[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return upper, mid, lower


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


def true_range(candles):
    trs = [None] * len(candles)
    for i in range(len(candles)):
        if i == 0:
            trs[i] = candles[i]["high"] - candles[i]["low"]
            continue
        high, low = candles[i]["high"], candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        trs[i] = max(high - low, abs(high - prev_close), abs(low - prev_close))
    return trs


def atr(candles, period):
    trs = true_range(candles)
    out = [None] * len(candles)
    if len(candles) < period + 1:
        return out
    first_avg = sum(trs[1:period + 1]) / period
    out[period] = first_avg
    prev = first_avg
    for i in range(period + 1, len(candles)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def volume_confirmation(volumes, lookback, multiplier):
    if len(volumes) < lookback + 1:
        return False, 0.0
    recent = volumes[-1]
    avg = sum(volumes[-(lookback + 1):-1]) / lookback
    if avg == 0:
        return False, 0.0
    ratio = recent / avg
    return ratio >= multiplier, ratio


# ==================== Sistema 1: Zona de giro ====================
def evaluate_reversal(symbol, candles_15m):
    live_price = candles_15m[-1]["close"]
    closed = closed_only(candles_15m)
    closes = [c["close"] for c in closed]
    volumes = [c["volume"] for c in closed]

    rsi_arr = rsi(closes, RSI_PERIOD)
    rsi_now = last_valid(rsi_arr)
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes, BB_PERIOD, BB_STD)
    bb_upper_now, bb_lower_now = last_valid(bb_upper), last_valid(bb_lower)
    atr_arr = atr(closed, ATR_PERIOD)
    atr_now = last_valid(atr_arr)
    vol_confirmed, vol_ratio = volume_confirmation(volumes, VOLUME_LOOKBACK, VOLUME_MULTIPLIER)

    last_candle = closed[-1] if closed else None
    candle_dir = None
    if last_candle:
        if last_candle["close"] > last_candle["open"]:
            candle_dir = "bull"
        elif last_candle["close"] < last_candle["open"]:
            candle_dir = "bear"

    direction = None
    if (rsi_now is not None and rsi_now <= RSI_LOW
            and bb_lower_now is not None and closes[-1] <= bb_lower_now
            and vol_confirmed and candle_dir == "bull"):
        direction = "bull"
    elif (rsi_now is not None and rsi_now >= RSI_HIGH
            and bb_upper_now is not None and closes[-1] >= bb_upper_now
            and vol_confirmed and candle_dir == "bear"):
        direction = "bear"

    return {
        "symbol": symbol, "price": live_price, "rsi": rsi_now,
        "vol_ratio": vol_ratio, "atr": atr_now, "direction": direction,
        "last_closed_candle_time": closed[-1]["time"] if closed else None,
    }


# ==================== Sistema 2: Estado de tendencia ====================
def evaluate_trend(symbol, candles_4h, candles_1d):
    closed_4h = closed_only(candles_4h)
    closes_4h = [c["close"] for c in closed_4h]

    fast_arr = sma(closes_4h, MA_FAST)
    slow_arr = sma(closes_4h, MA_SLOW)
    cross = cross_state(fast_arr, slow_arr)
    cross_kind = "bull" if cross in ("bull_cross", "above") else "bear" if cross in ("bear_cross", "below") else "neutral"

    atr_arr = atr(closed_4h, ATR_PERIOD)
    atr_now = last_valid(atr_arr)
    momentum_kind = "neutral"
    if atr_now and len(closes_4h) > MOMENTUM_LOOKBACK:
        change = closes_4h[-1] - closes_4h[-1 - MOMENTUM_LOOKBACK]
        if change > MOMENTUM_ATR_MULTIPLIER * atr_now:
            momentum_kind = "bull"
        elif change < -MOMENTUM_ATR_MULTIPLIER * atr_now:
            momentum_kind = "bear"

    kinds = [cross_kind, momentum_kind]

    daily_kind = "neutral"
    if USE_DAILY_CONFIRMATION:
        closed_1d = closed_only(candles_1d)
        closes_1d = [c["close"] for c in closed_1d]
        sma_daily_arr = sma(closes_1d, DAILY_SMA)
        sma_daily_now = last_valid(sma_daily_arr)
        if sma_daily_now and closes_1d:
            daily_kind = "bull" if closes_1d[-1] > sma_daily_now else "bear" if closes_1d[-1] < sma_daily_now else "neutral"
        kinds.append(daily_kind)

    bull_n, bear_n = kinds.count("bull"), kinds.count("bear")
    if bull_n > bear_n and bull_n >= 2:
        status = "alcista"
    elif bear_n > bull_n and bear_n >= 2:
        status = "bajista"
    else:
        status = "neutral"

    return {
        "symbol": symbol, "status": status, "cross_kind": cross_kind,
        "momentum_kind": momentum_kind, "daily_kind": daily_kind,
    }


# ==================== Telegram ====================
def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        print(text)
        return
    url = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    if not r.ok:
        print(f"[ERROR] Telegram respondió {r.status_code}: {r.text}")


def format_reversal_alert(result, trend, tier):
    label = result["symbol"].replace("USDT", "")
    icon = "🟢" if result["direction"] == "bull" else "🔴"
    tag = "posible zona de compra (giro alcista)" if result["direction"] == "bull" else "posible zona de venta (giro bajista)"
    tier_txt = {
        "fuerte": "✅ *la tendencia mayor CONFIRMA este giro*",
        "moderada": "➖ tendencia mayor neutral, sin oponerse",
        "contraria": "⚠️ *la tendencia mayor va EN CONTRA de este giro* — más riesgo",
    }[tier]
    return (
        f"{icon} *{label}* — {tag}\n"
        f"Precio: ${result['price']:,.2f}\n"
        f"RSI(14): {result['rsi']:.1f}\n"
        f"Volumen: {result['vol_ratio']:.2f}x el promedio\n"
        f"Tendencia mayor (4h/1d): *{trend['status']}*\n"
        f"{tier_txt}\n"
        f"Timeframe de la señal: {REVERSAL_INTERVAL} (vela cerrada)\n\n"
        f"_Watch-only. Vos decidís qué hacer con esto._"
    )


def format_trend_change(symbol, prev_status, new_status):
    label = symbol.replace("USDT", "")
    icon = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}[new_status]
    return (
        f"{icon} *{label}* — cambio de tendencia mayor\n"
        f"{prev_status or 'sin dato previo'} → *{new_status}*\n"
        f"_Basado en 4h + 1d. Esto es informativo, no una alerta de entrada._"
    )


# ==================== Registro para medir aciertos ====================
def load_signals():
    if not os.path.exists(SIGNALS_LOG_FILE):
        return []
    signals = []
    with open(SIGNALS_LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                signals.append(json.loads(line))
    return signals


def save_signals(signals):
    with open(SIGNALS_LOG_FILE, "w") as f:
        for s in signals:
            f.write(json.dumps(s) + "\n")


def new_signal_record(result, tier):
    entry_price = result["price"]
    direction = result["direction"]
    atr_val = result["atr"]
    if atr_val:
        if direction == "bull":
            target_price = entry_price + atr_val * TARGET_ATR_MULT
            stop_price = entry_price - atr_val * STOP_ATR_MULT
        else:
            target_price = entry_price - atr_val * TARGET_ATR_MULT
            stop_price = entry_price + atr_val * STOP_ATR_MULT
    else:
        pct = 1.5 / 100
        target_price = entry_price * (1 + pct) if direction == "bull" else entry_price * (1 - pct)
        stop_price = entry_price * (1 - pct) if direction == "bull" else entry_price * (1 + pct)

    return {
        "id": f"{result['symbol']}-{result['last_closed_candle_time']}-{direction}",
        "symbol": result["symbol"], "direction": direction, "tier": tier,
        "signal_candle_time": result["last_closed_candle_time"],
        "entry_price": entry_price, "target_price": target_price, "stop_price": stop_price,
        "horizon_candles": HORIZON_CANDLES, "status": "open", "outcome": None,
        "exit_price": None, "pct_result": None, "candles_elapsed": 0,
    }


def resolve_open_signals(open_signals_for_symbol, closed_candles):
    resolved_now = []
    for signal in open_signals_for_symbol:
        after = [c for c in closed_candles if c["time"] > signal["signal_candle_time"]]
        if not after:
            continue
        for i, c in enumerate(after, start=1):
            if signal["direction"] == "bull":
                hit_target = c["high"] >= signal["target_price"]
                hit_stop = c["low"] <= signal["stop_price"]
            else:
                hit_target = c["low"] <= signal["target_price"]
                hit_stop = c["high"] >= signal["stop_price"]

            outcome, exit_price = None, None
            if hit_target and hit_stop:
                outcome, exit_price = "stop_hit", signal["stop_price"]
            elif hit_target:
                outcome, exit_price = "target_hit", signal["target_price"]
            elif hit_stop:
                outcome, exit_price = "stop_hit", signal["stop_price"]
            elif i >= signal["horizon_candles"]:
                outcome, exit_price = "timeout", c["close"]

            if outcome:
                signal["status"] = "closed"
                signal["outcome"] = outcome
                signal["exit_price"] = exit_price
                sign = 1 if signal["direction"] == "bull" else -1
                signal["pct_result"] = ((exit_price - signal["entry_price"]) / signal["entry_price"]) * 100 * sign
                signal["candles_elapsed"] = i
                resolved_now.append(signal)
                break
        else:
            signal["candles_elapsed"] = len(after)
    return resolved_now


def format_resolution_message(signal):
    label = signal["symbol"].replace("USDT", "")
    icon = {"target_hit": "✅", "stop_hit": "❌", "timeout": "⏱"}[signal["outcome"]]
    outcome_txt = {"target_hit": "TARGET alcanzado", "stop_hit": "STOP alcanzado", "timeout": "sin definir (timeout)"}[signal["outcome"]]
    return (
        f"{icon} *{label}* ({signal['direction']}, tier {signal['tier']}) — {outcome_txt}\n"
        f"Entrada: ${signal['entry_price']:,.2f} → Salida: ${signal['exit_price']:,.2f}\n"
        f"Resultado: {signal['pct_result']:+.2f}%"
    )


def compute_stats(signals):
    closed = [s for s in signals if s["status"] == "closed"]
    if not closed:
        return None
    decided = [s for s in closed if s["outcome"] in ("target_hit", "stop_hit")]
    wins = [s for s in decided if s["outcome"] == "target_hit"]
    win_rate = (len(wins) / len(decided) * 100) if decided else None
    avg_pct = sum(s["pct_result"] for s in closed) / len(closed)
    return {"total_closed": len(closed), "win_rate_pct": win_rate, "avg_pct_result": avg_pct,
            "open_count": len([s for s in signals if s["status"] == "open"])}


# ==================== State y resumen diario ====================
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


def maybe_send_daily_summary(state, signals):
    meta = state.setdefault("_meta", {})
    last_ts = meta.get("last_summary_ts")
    now = time.time()
    if last_ts is not None and (now - last_ts) < SUMMARY_INTERVAL_HOURS * 3600:
        return
    stats = compute_stats(signals)
    if stats:
        wr_txt = f"{stats['win_rate_pct']:.1f}%" if stats["win_rate_pct"] is not None else "—"
        body = (
            f"📊 *Resumen diario — Crypto Signal Monitor*\n\n"
            f"Bot activo. Señales cerradas: {stats['total_closed']} · abiertas: {stats['open_count']}\n"
            f"Win rate: {wr_txt} · retorno promedio: {stats['avg_pct_result']:+.2f}%\n\n"
            f"_Watch-only. No es consejo financiero._"
        )
    else:
        body = "📊 *Resumen diario — Crypto Signal Monitor*\n\nBot activo. Todavía sin señales cerradas."
    send_telegram(body)
    meta["last_summary_ts"] = now


# ==================== Main ====================
def main():
    state = load_state()
    signals = load_signals()
    any_sent = False

    for symbol in SYMBOLS:
        symbol = symbol.strip()
        try:
            candles_15m = fetch_klines(symbol, REVERSAL_INTERVAL)
            candles_4h = fetch_klines(symbol, TREND_INTERVAL)
            candles_1d = fetch_klines(symbol, DAILY_INTERVAL) if USE_DAILY_CONFIRMATION else []
        except Exception as e:
            print(f"[ERROR] {symbol}: no se pudo obtener datos ({e})")
            continue

        if len(closed_only(candles_15m)) < max(RSI_PERIOD, BB_PERIOD) + 1:
            print(f"[WARN] {symbol}: pocas velas de 15m todavía, se salta.")
            continue
        if len(closed_only(candles_4h)) < max(MA_SLOW, ATR_PERIOD) + 1:
            print(f"[WARN] {symbol}: pocas velas de 4h todavía, se salta.")
            continue

        # --- Sistema 2: estado de tendencia ---
        trend = evaluate_trend(symbol, candles_4h, candles_1d)
        symbol_state = state.get(symbol, {})
        prev_trend_status = symbol_state.get("trend_status")
        if prev_trend_status is not None and trend["status"] != prev_trend_status:
            send_telegram(format_trend_change(symbol, prev_trend_status, trend["status"]))
        symbol_state["trend_status"] = trend["status"]

        # --- Sistema 1: zona de giro ---
        reversal = evaluate_reversal(symbol, candles_15m)

        # --- Resolver señales de giro ya abiertas ---
        open_for_symbol = [s for s in signals if s["symbol"] == symbol and s["status"] == "open"]
        resolved = resolve_open_signals(open_for_symbol, closed_only(candles_15m))
        if resolved and NOTIFY_ON_RESOLUTION:
            for s in resolved:
                send_telegram(format_resolution_message(s))

        # --- Nueva alerta de giro, con el tier según la tendencia vigente ---
        prev_direction = symbol_state.get("reversal_direction")
        prev_candle_time = symbol_state.get("reversal_candle_time")
        same_candle = prev_candle_time == reversal["last_closed_candle_time"]
        should_alert = reversal["direction"] and not (reversal["direction"] == prev_direction and same_candle)

        if should_alert:
            if trend["status"] == "alcista" and reversal["direction"] == "bull":
                tier = "fuerte"
            elif trend["status"] == "bajista" and reversal["direction"] == "bear":
                tier = "fuerte"
            elif trend["status"] == "neutral":
                tier = "moderada"
            else:
                tier = "contraria"

            send_telegram(format_reversal_alert(reversal, trend, tier))
            signals.append(new_signal_record(reversal, tier))
            any_sent = True

        symbol_state["reversal_direction"] = reversal["direction"]
        symbol_state["reversal_candle_time"] = reversal["last_closed_candle_time"]
        state[symbol] = symbol_state

        print(
            f"{symbol}: price={reversal['price']:.2f} rsi={reversal['rsi']} "
            f"vol_ratio={reversal['vol_ratio']:.2f} reversal={reversal['direction']} "
            f"| trend={trend['status']} (cross={trend['cross_kind']}, mom={trend['momentum_kind']}, "
            f"daily={trend['daily_kind']}) | resolved_this_run={len(resolved)}"
        )

    maybe_send_daily_summary(state, signals)
    save_state(state)
    save_signals(signals)
    if not any_sent:
        print("Sin alertas de giro nuevas en este ciclo.")


if __name__ == "__main__":
    sys.exit(main())
