#!/usr/bin/env python3
"""
Crypto Checkpoint Report — chequeo periódico de fiabilidad
------------------------------------------------------------
Lee signals_log.jsonl (el historial que ya arma crypto_monitor.py) y manda
un resumen a Telegram en lenguaje simple: win rate, retorno promedio, y
algunas señales de alerta si algo se ve raro (para que sepas cuándo vale
la pena volver a hablarlo, no para que hagas estadística vos).

NO ajusta nada solo. Es información para decidir, no un piloto automático.
"""

import os
import sys
import json
import requests

SIGNALS_LOG_FILE = os.environ.get("SIGNALS_LOG_FILE", "signals_log.jsonl")
MIN_SAMPLES_FOR_FLAGS = int(os.environ.get("MIN_SAMPLES_FOR_FLAGS", 8))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


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


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        print(text)
        return
    url = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=20)
    if not r.ok:
        print(f"[ERROR] Telegram respondió {r.status_code}: {r.text}")


def win_rate(closed):
    decided = [s for s in closed if s["outcome"] in ("target_hit", "stop_hit")]
    wins = [s for s in decided if s["outcome"] == "target_hit"]
    if not decided:
        return None, 0, 0
    return len(wins) / len(decided) * 100, len(wins), len(decided)


def group_stats(closed, key_fn, min_n=3):
    buckets = {}
    for s in closed:
        buckets.setdefault(key_fn(s), []).append(s)
    out = {}
    for key, items in buckets.items():
        if len(items) < min_n:
            continue
        wr, wins, decided_n = win_rate(items)
        avg = sum(s["pct_result"] for s in items) / len(items)
        out[key] = {"n": len(items), "win_rate": wr, "avg_pct": avg}
    return out


def main():
    signals = load_signals()
    closed = [s for s in signals if s["status"] == "closed"]
    open_n = len(signals) - len(closed)

    lines = ["📋 *Checkpoint del bot de cripto*", ""]
    lines.append(f"Señales cerradas: {len(closed)} · abiertas ahora: {open_n}")
    lines.append("")

    if len(closed) < MIN_SAMPLES_FOR_FLAGS:
        lines.append(
            f"Todavía hay pocas señales cerradas ({len(closed)}) para sacar "
            f"conclusiones — con menos de {MIN_SAMPLES_FOR_FLAGS} casos, "
            f"cualquier número puede ser pura casualidad. Mejor esperar a que "
            f"se acumulen más antes de tocar nada."
        )
        send_telegram("\n".join(lines))
        print("Checkpoint enviado (sin datos suficientes todavía).")
        return

    overall_wr, wins, decided_n = win_rate(closed)
    avg_pct = sum(s["pct_result"] for s in closed) / len(closed)
    timeouts = len([s for s in closed if s["outcome"] == "timeout"])

    lines.append("*General:*")
    if overall_wr is not None:
        lines.append(f"• Win rate: {overall_wr:.0f}% ({wins} de {decided_n} señales decididas)")
    lines.append(f"• Retorno promedio por señal: {avg_pct:+.2f}%")
    lines.append(f"• Timeouts (no se definieron a tiempo): {timeouts} de {len(closed)}")
    lines.append("")

    by_symbol = group_stats(closed, lambda s: s["symbol"])
    if by_symbol:
        lines.append("*Por moneda:*")
        for symbol, s in sorted(by_symbol.items(), key=lambda kv: -kv[1]["n"]):
            wr_txt = f"{s['win_rate']:.0f}%" if s["win_rate"] is not None else "—"
            lines.append(f"• {symbol}: win rate {wr_txt}, retorno prom. {s['avg_pct']:+.2f}% (n={s['n']})")
        lines.append("")

    by_direction = group_stats(closed, lambda s: s["direction"])
    if by_direction:
        lines.append("*Por dirección:*")
        for direction, s in sorted(by_direction.items(), key=lambda kv: -kv[1]["n"]):
            wr_txt = f"{s['win_rate']:.0f}%" if s["win_rate"] is not None else "—"
            lines.append(f"• {direction}: win rate {wr_txt}, retorno prom. {s['avg_pct']:+.2f}% (n={s['n']})")
        lines.append("")

    # ---- Señales de alerta (heurísticas simples, no un juicio definitivo) ----
    flags = []
    if overall_wr is not None and overall_wr < 40 and decided_n >= MIN_SAMPLES_FOR_FLAGS:
        flags.append(
            f"⚠️ Win rate general por debajo del 40% ({overall_wr:.0f}%) — "
            f"vale la pena traer este reporte a una conversación y revisar "
            f"los umbrales juntos."
        )
    if timeouts / len(closed) > 0.4:
        flags.append(
            f"⚠️ Más del 40% de las señales terminan en timeout sin definirse "
            f"— podría indicar que el horizonte de tiempo o el target/stop "
            f"no están bien calibrados para el movimiento real del mercado."
        )
    for direction, s in by_direction.items():
        if s["win_rate"] is not None and s["win_rate"] < 35:
            flags.append(
                f"⚠️ Las señales '{direction}' vienen con win rate bajo "
                f"({s['win_rate']:.0f}%, n={s['n']}) — podría valer la pena "
                f"revisar si ese lado necesita un filtro extra."
            )

    if flags:
        lines.append("*Cosas para revisar:*")
        lines.extend(flags)
        lines.append("")
    else:
        lines.append("Sin señales de alerta puntuales por ahora — los números se ven parejos.")
        lines.append("")

    lines.append(
        "_Si algo de esto te llama la atención, pegá este reporte en una "
        "conversación y lo analizamos juntos con más detalle. El bot no "
        "ajusta nada solo._"
    )

    send_telegram("\n".join(lines))
    print(f"Checkpoint enviado. {len(closed)} señales cerradas analizadas.")


if __name__ == "__main__":
    sys.exit(main())
