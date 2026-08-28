#!/usr/bin/env python3
"""
Crypto News Calibration Report — bajo demanda
------------------------------------------------
Lee crypto_news_impact_log.jsonl y manda a Telegram:
  - Promedios por moneda y por tipo de evento (palabra clave), separando
    noticias de una moneda puntual (comparadas contra la otra moneda)
    de las noticias macro (promedio BTC+ETH)
  - Un listado de las últimas noticias YA resueltas con su resultado
    individual — no solo promedios

Es diagnóstico, no un filtro automático.
"""

import os
import sys
import time
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


def relative_time(ts):
    hours = max(0, (time.time() - ts) / 3600)
    if hours < 24:
        return f"hace {int(hours)}h"
    return f"hace {int(hours // 24)}d"


def format_resolved_item(e):
    label = SYMBOL_LABELS.get(e["symbol"], e["symbol"])
    value = e["excess_pct_change"] if e["symbol"] != "macro" else e["market_pct_change"]
    icon = "🟢" if value >= 0 else "🔴"
    kw_txt = f" [{', '.join(e['keywords'])}]" if e["keywords"] else ""
    return f"{icon} {label}{kw_txt}: {value:+.2f}% — [{e['title'][:60]}]({e['link']}) ({relative_time(e['published_at'])})"


def main():
    events = cni.load_impact_log()
    summary = cni.compute_calibration(events, min_samples=MIN_SAMPLES)

    lines = ["📐 *Calibración de impacto — noticias cripto*", ""]
    lines.append(f"Eventos cerrados: {summary['total_closed']} · abiertos: {summary['total_open']}")
    lines.append(f"_(umbral mínimo para mostrar promedios: {MIN_SAMPLES} casos por grupo)_")
    lines.append("")

    if summary["total_closed"] == 0:
        lines.append(
            "Todavía no hay ningún evento resuelto. Se va llenando solo "
            "con el tiempo — no hace falta hacer nada."
        )
        send_telegram("\n".join(lines))
        print("Reporte enviado (sin datos todavía).")
        return

    if summary["by_symbol"]:
        lines.append("*Por moneda* (efecto propio, vs. la otra moneda en la misma ventana)")
        for symbol, s in sorted(summary["by_symbol"].items(), key=lambda kv: -kv[1]["avg_abs"]):
            label = SYMBOL_LABELS.get(symbol, symbol)
            lines.append(f"• {label}: {s['avg_signed']:+.2f}% promedio (|{s['avg_abs']:.2f}%| típico, n={s['n']})")
        lines.append("")

    if summary["by_keyword_coin"]:
        lines.append("*Por tipo de evento — noticias de una moneda puntual*")
        for kw, s in sorted(summary["by_keyword_coin"].items(), key=lambda kv: -kv[1]["avg_abs"]):
            base = f"• {kw}: {s['avg_signed']:+.2f}% promedio (|{s['avg_abs']:.2f}%| típico, n={s['n']})"
            if "hit_rate" in s:
                base += f" — acertó dirección {s['hit_rate']:.0f}% de las veces"
            lines.append(base)
        lines.append("")

    if summary["by_keyword_macro"]:
        lines.append("*Por tipo de evento — noticias macro (promedio BTC+ETH)*")
        for kw, s in sorted(summary["by_keyword_macro"].items(), key=lambda kv: -kv[1]["avg_abs"]):
            base = f"• {kw}: {s['avg_signed']:+.2f}% promedio (|{s['avg_abs']:.2f}%| típico, n={s['n']})"
            if "hit_rate" in s:
                base += f" — acertó dirección {s['hit_rate']:.0f}% de las veces"
            lines.append(base)
        lines.append("")

    if summary["recent_resolved"]:
        lines.append("*Últimas noticias resueltas (resultado individual):*")
        for e in summary["recent_resolved"]:
            lines.append(format_resolved_item(e))
        lines.append("")

    lines.append(
        "_Diagnóstico: correlación, no causalidad. Con muestras chicas, "
        "cualquier promedio puede ser ruido._"
    )
    send_telegram("\n".join(lines))
    print(f"Reporte enviado. {summary['total_closed']} eventos analizados.")


if __name__ == "__main__":
    sys.exit(main())
