#!/usr/bin/env python3
"""
Crypto News Calibration Report — bajo demanda
------------------------------------------------
Lee crypto_news_impact_log.jsonl y manda a Telegram un resumen de qué
tipo de noticia de cripto realmente movió el precio, en exceso sobre el
market cap total del mercado (para aislar el efecto propio de la moneda).

Es diagnóstico, no un filtro automático.
"""

import os
import sys
import requests
import crypto_news_impact as cni

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
MIN_SAMPLES = int(os.environ.get("CRYPTO_CALIBRATION_MIN_SAMPLES", 3))
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

SYMBOL_LABELS = {"BTCUSDT": "🟠 Bitcoin", "ETHUSDT": "🔷 Ethereum", "macro": "🌐 Macro cripto"}


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        print(text)
        return
    url = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=20)
    if not r.ok:
        print(f"[ERROR] Telegram respondió {r.status_code}: {r.text}")


def main():
    events = cni.load_impact_log()
    summary = cni.compute_calibration(events, min_samples=MIN_SAMPLES)

    lines = ["📐 *Calibración de impacto — noticias cripto*", ""]
    lines.append(f"Eventos cerrados: {summary['total_closed']} · abiertos: {summary['total_open']}")
    lines.append(f"_(umbral mínimo para mostrar: {MIN_SAMPLES} casos por grupo)_")
    lines.append("")

    if summary["total_closed"] < MIN_SAMPLES:
        lines.append(
            "Todavía no hay suficientes eventos resueltos para calibrar nada "
            "en serio. Se va llenando solo con el tiempo — no hace falta "
            "hacer nada."
        )
        send_telegram("\n".join(lines))
        print("Reporte enviado (sin datos suficientes todavía).")
        return

    if summary["by_symbol"]:
        lines.append("*Por moneda* (movimiento en exceso sobre el mercado cripto general)")
        for symbol, s in sorted(summary["by_symbol"].items(), key=lambda kv: -kv[1]["avg_abs_excess"]):
            label = SYMBOL_LABELS.get(symbol, symbol)
            lines.append(f"• {label}: {s['avg_signed_excess']:+.2f}% promedio (|{s['avg_abs_excess']:.2f}%| típico, n={s['n']})")
        lines.append("")

    if summary["by_keyword"]:
        lines.append("*Por tipo de evento*")
        for kw, s in sorted(summary["by_keyword"].items(), key=lambda kv: -kv[1]["avg_abs_excess"]):
            base = f"• {kw}: {s['avg_signed_excess']:+.2f}% promedio (|{s['avg_abs_excess']:.2f}%| típico, n={s['n']})"
            if "hit_rate" in s:
                base += f" — acertó dirección {s['hit_rate']:.0f}% de las veces"
            lines.append(base)
        lines.append("")

    lines.append(
        "_Diagnóstico: correlación, no causalidad. Con muestras chicas, "
        "cualquier promedio puede ser ruido._"
    )
    send_telegram("\n".join(lines))
    print(f"Reporte enviado. {summary['total_closed']} eventos analizados.")


if __name__ == "__main__":
    sys.exit(main())
