#!/usr/bin/env python3
"""
Crypto Signal Monitor — watch-only alert bot
----------------------------------------------
Monitorea BTC y ETH en Binance, calcula RSI, cruce de medias móviles y
momentum sobre velas CERRADAS (evita repintado), y manda una alerta por
Telegram cuando 3 de 4 señales técnicas se alinean, con volumen y
tendencia mayor como filtros obligatorios.

NO ejecuta órdenes. NO es consejo financiero. Solo vigila y avisa.
"""

import os
import json
import sys
import time
import requests

# ---------- Config ----------
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

SIGNALS_LOG_FILE = os.environ.get("SIGNALS_LOG_FILE", "signals_log.jsonl")
TARGET_PCT = float(os.environ.get("TARGET_PCT", 1.5))
STOP_PCT = float(os.environ.get("STOP_PCT", 1.0))
HORIZON_CANDLES = int(os.environ.get("HORIZON_CANDLES", 4))
NOTIFY_ON_RESOLUTION = os.environ.get("NOTIFY_ON_RESOLUTION", "true").lower() == "true"
SUMMARY_INTERVAL_HOURS = float(os.environ.get("SUMMARY_INTERVAL_HOURS", 24))

BB_PERIOD = int(os.environ.get("BB_PERIOD", 20))
BB_STD = float(os.environ.get("BB_STD", 2.0))

ATR_PERIOD = int(os.environ.get("ATR_PERIOD", 14))
USE_ATR_TARGETS = os.environ.get("USE_ATR_TARGETS", "true").lower() == "true"
TARGET_ATR_MULT = float(os.environ.get("TARGET_ATR_MULT", 1.5))
STOP_ATR_MULT = float(os.environ.get("STOP_ATR_MULT", 1.0))

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


def bollinger_bands(values, period=BB_PERIOD, num_std=BB_STD):
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


def atr(candles, period=ATR_PERIOD):
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

    bb_upper, bb_mid, bb_lower = bollinger_bands(closes, BB_PERIOD, BB_STD)
    bb_upper_now, bb_lower_now = last_valid(bb_upper), last_valid(bb_lower)
    atr_arr = atr(closed, ATR_PERIOD)
    atr_now = last_valid(atr_arr)

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

    bb_kind = "neutral"
    if bb_lower_now is not None and closes[-1] <= bb_lower_now:
        bb_kind = "bull"
    elif bb_upper_now is not None and closes[-1] >= bb_upper_now:
        bb_kind = "bear"

    signal_kinds = [rsi_kind, cross_kind, mom_kind, bb_kind]
    bull = signal_kinds.count("bull")
    bear = signal_kinds.count("bear")

    base_direction = None
    if bull >= 3:
        base_direction = "bull"
    elif bear >= 3:
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
        "symbol": symbol,
        "price": live_price,
        "closed_close": closes[-1] if closes else live_price,
        "rsi": rsi_now,
        "cross": cross,
        "momentum": mom,
        "bb_kind": bb_kind,
        "atr": atr_now,
        "volume_confirmed": vol_confirmed,
        "volume_ratio": vol_ratio,
        "base_direction": base_direction,
        "htf_trend": htf_trend,
        "direction": direction,
        "blocked_by": gate_reasons,
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
    bb_txt = {"bull": "precio en banda inferior", "bear": "precio en banda superior", "neutral": "dentro de banda"}[result["bb_kind"]]
    atr_txt = f"${result['atr']:,.2f}" if result["atr"] else "—"
    return (
        f"{icon} *{label}* — {tag}\n"
        f"Precio: ${result['price']:,.2f}\n"
        f"RSI({RSI_PERIOD}): {rsi_txt}\n"
        f"Medias {MA_FAST}/{MA_SLOW}: {result['cross']}\n"
        f"Momentum: {result['momentum']:+.2f}%\n"
        f"Bollinger({BB_PERIOD}): {bb_txt}\n"
        f"Volumen vs. promedio: {vol_txt}\n"
        f"Tendencia {HTF_INTERVAL}: {result['htf_trend']} (alineada) ✅\n"
        f"ATR({ATR_PERIOD}): {atr_txt} (target/stop calculados con esto)\n"
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


def new_signal_record(result):
    entry_price = result["price"]
    direction = result["direction"]
    atr_val = result.get("atr")

    if USE_ATR_TARGETS and atr_val:
        sizing_method = "atr"
        if direction == "bull":
            target_price = entry_price + atr_val * TARGET_ATR_MULT
            stop_price = entry_price - atr_val * STOP_ATR_MULT
        else:
            target_price = entry_price - atr_val * TARGET_ATR_MULT
            stop_price = entry_price + atr_val * STOP_ATR_MULT
    else:
        sizing_method = "fixed_pct"
        if direction == "bull":
            target_price = entry_price * (1 + TARGET_PCT / 100)
            stop_price = entry_price * (1 - STOP_PCT / 100)
        else:
            target_price = entry_price * (1 - TARGET_PCT / 100)
            stop_price = entry_price * (1 + STOP_PCT / 100)

    target_pct = abs(target_price - entry_price) / entry_price * 100
    stop_pct = abs(stop_price - entry_price) / entry_price * 100

    return {
        "id": f"{result['symbol']}-{result['last_closed_candle_time']}-{direction}",
        "symbol": result["symbol"],
        "direction": direction,
        "signal_candle_time": result["last_closed_candle_time"],
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "target_pct": round(target_pct, 3),
        "stop_pct": round(stop_pct, 3),
        "sizing_method": sizing_method,
        "atr_at_signal": atr_val,
        "horizon_candles": HORIZON_CANDLES,
        "status": "open",
        "outcome": None,
        "exit_price": None,
        "pct_result": None,
        "candles_elapsed": 0,
    }


def resolve_open_signals(open_signals_for_symbol, closed_candles):
    """
    Revisa cada señal abierta contra las velas cerradas que llegaron
    DESPUÉS de que se generó la señal. Usa el high/low de cada vela para
    ver si tocó el target o el stop primero.

    Limitación honesta: si target y stop se tocan dentro de la MISMA vela,
    no hay forma de saber cuál pasó primero con datos de vela cerrada —
    en ese caso, por seguridad, se asume que el stop se tocó primero
    (supuesto conservador, evita inflar artificialmente la tasa de éxito).
    """
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


def compute_stats(signals):
    closed = [s for s in signals if s["status"] == "closed"]
    if not closed:
        return None
    wins = [s for s in closed if s["outcome"] == "target_hit"]
    losses = [s for s in closed if s["outcome"] == "stop_hit"]
    timeouts = [s for s in closed if s["outcome"] == "timeout"]
    decided = wins + losses
    win_rate = (len(wins) / len(decided) * 100) if decided else None
    avg_pct = sum(s["pct_result"] for s in closed) / len(closed)
    return {
        "total_closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(timeouts),
        "win_rate_pct": win_rate,
        "avg_pct_result": avg_pct,
        "open_count": len([s for s in signals if s["status"] == "open"]),
    }


def format_resolution_message(signal):
    label = signal["symbol"].replace("USDT", "")
    outcome_icon = {"target_hit": "✅", "stop_hit": "❌", "timeout": "⏱"}[signal["outcome"]]
    outcome_txt = {
        "target_hit": "TARGET alcanzado",
        "stop_hit": "STOP alcanzado",
        "timeout": "sin definir a tiempo (timeout)",
    }[signal["outcome"]]
    return (
        f"{outcome_icon} *{label}* ({signal['direction']}) — {outcome_txt}\n"
        f"Entrada: ${signal['entry_price']:,.2f} → Salida: ${signal['exit_price']:,.2f}\n"
        f"Resultado: {signal['pct_result']:+.2f}%\n"
        f"_Registro de fiabilidad, no una operación real._"
    )


def maybe_send_daily_summary(state, signals):
    meta = state.setdefault("_meta", {})
    last_ts = meta.get("last_summary_ts")
    now = time.time()
    if last_ts is not None and (now - last_ts) < SUMMARY_INTERVAL_HOURS * 3600:
        return

    stats = compute_stats(signals)
    open_count = len([s for s in signals if s["status"] == "open"])
    total_signals = len(signals)

    if stats:
        win_rate_txt = f"{stats['win_rate_pct']:.1f}%" if stats["win_rate_pct"] is not None else "—"
        body = (
            f"📊 *Resumen diario — Crypto Signal Monitor*\n\n"
            f"Bot activo, corriendo cada 5 min.\n"
            f"Señales totales generadas: {total_signals}\n"
            f"Abiertas ahora: {open_count}\n"
            f"Cerradas: {stats['total_closed']} "
            f"(✅ {stats['wins']} · ❌ {stats['losses']} · ⏱ {stats['timeouts']})\n"
            f"Win rate: {win_rate_txt}\n"
            f"Retorno promedio por señal: {stats['avg_pct_result']:+.2f}%\n\n"
            f"_Solo watch-only. Esto no es una recomendación de inversión._"
        )
    else:
        body = (
            f"📊 *Resumen diario — Crypto Signal Monitor*\n\n"
            f"Bot activo, corriendo cada 5 min. Todavía no se generó "
            f"ninguna señal (los filtros son exigentes a propósito) — "
            f"nada para reportar por ahora."
        )

    send_telegram(body)
    meta["last_summary_ts"] = now


def main():
    state = load_state()
    signals = load_signals()
    any_sent = False

    for symbol in SYMBOLS:
        symbol = symbol.strip()
        try:
            candles = fetch_klines(symbol, INTERVAL)
        except Exception as e:
            print(f"[ERROR] {symbol}: no se pudo obtener datos ({e})")
            continue

        closed_candles = [c for c in candles if c["is_closed"]]
        min_needed = max(RSI_PERIOD, MA_SLOW, BB_PERIOD, ATR_PERIOD) + 1
        if len(closed_candles) < min_needed:
            print(f"[WARN] {symbol}: no hay suficientes velas cerradas todavía, se salta este ciclo.")
            continue

        open_for_symbol = [s for s in signals if s["symbol"] == symbol and s["status"] == "open"]
        resolved = resolve_open_signals(open_for_symbol, closed_candles)
        if resolved and NOTIFY_ON_RESOLUTION:
            for s in resolved:
                send_telegram(format_resolution_message(s))

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
            signals.append(new_signal_record(result))
            any_sent = True

        state[symbol] = {
            "direction": result["direction"],
            "candle_time": result["last_closed_candle_time"],
        }

        print(
            f"{symbol}: price={result['price']:.2f} rsi={result['rsi']} "
            f"cross={result['cross']} mom={result['momentum']:.2f}% "
            f"bb={result['bb_kind']} atr={result['atr']} "
            f"vol_ratio={result['volume_ratio']:.2f} "
            f"base={result['base_direction']} htf_trend={result['htf_trend']} "
            f"final={result['direction']} "
            f"blocked_by={result['blocked_by'] or '-'} "
            f"resolved_this_run={len(resolved)}"
        )

    maybe_send_daily_summary(state, signals)

    save_state(state)
    save_signals(signals)

    stats = compute_stats(signals)
    if stats:
        print(
            f"[STATS] cerradas={stats['total_closed']} "
            f"wins={stats['wins']} losses={stats['losses']} timeouts={stats['timeouts']} "
            f"win_rate={stats['win_rate_pct']:.1f}%" if stats['win_rate_pct'] is not None
            else f"[STATS] cerradas={stats['total_closed']} sin datos suficientes para win rate"
        )
        print(f"[STATS] retorno promedio por señal: {stats['avg_pct_result']:+.2f}% | abiertas: {stats['open_count']}")
    else:
        print("[STATS] todavía no hay señales cerradas para calcular fiabilidad.")

    if not any_sent:
        print("Sin señales nuevas en este ciclo.")


if __name__ == "__main__":
    sys.exit(main())
