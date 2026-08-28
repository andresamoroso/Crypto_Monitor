#!/usr/bin/env python3
"""
Crypto News Digest — resumen de noticias relevantes para BTC/ETH
------------------------------------------------------------------------
Busca noticias recientes de Google News para Bitcoin, Ethereum, y temas
macro de cripto (regulación, ETFs, SEC), y te manda un resumen a Telegram
2 veces por día con lo que sea NUEVO desde la última corrida.

También registra cada noticia en el sistema de calibración de impacto
(crypto_news_impact.py), capturando el precio y el market cap del momento
en la misma corrida — sin depender de ningún otro script.

NO evalúa relevancia ni sentimiento. NO es consejo financiero.
"""

import os
import sys
import json
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
from email.utils import parsedate_to_datetime

import crypto_news_impact as cni

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = os.environ.get("CRYPTO_NEWS_STATE_FILE", "crypto_news_state.json")
MAX_ITEMS_PER_ASSET = int(os.environ.get("MAX_ITEMS_PER_ASSET", 4))
MAX_ITEMS_PER_MACRO_TOPIC = int(os.environ.get("MAX_ITEMS_PER_MACRO_TOPIC", 4))
MAX_ITEMS_PER_SECTION_IN_MSG = int(os.environ.get("MAX_ITEMS_PER_SECTION_IN_MSG", 8))
MAX_TOTAL_ITEMS_PER_MESSAGE = int(os.environ.get("MAX_TOTAL_ITEMS_PER_MESSAGE", 30))
SEEN_TTL_DAYS = int(os.environ.get("SEEN_TTL_DAYS", 20))

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

ASSETS = [("BTCUSDT", "Bitcoin"), ("ETHUSDT", "Ethereum")]
MACRO_QUERIES = [
    "cryptocurrency regulation",
    "bitcoin ETF",
    "crypto market crash OR rally",
    "SEC cryptocurrency",
]


def fetch_google_news(query, max_items=5):
    url = GOOGLE_NEWS_URL.format(query=quote(query))
    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:max_items]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = item.findtext("pubDate") or ""
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        try:
            pub_ts = parsedate_to_datetime(pub_date_raw).timestamp()
        except Exception:
            pub_ts = time.time()
        if not link:
            continue
        items.append({"title": title, "link": link, "source": source, "pub_ts": pub_ts})
    return items


def hash_link(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def relative_time(pub_ts):
    hours = max(0, (time.time() - pub_ts) / 3600)
    if hours < 1:
        return "hace <1h"
    if hours < 24:
        return f"hace {int(hours)}h"
    return f"hace {int(hours // 24)}d"


def format_news_line(item, label=None):
    prefix = f"*{label}:* " if label else ""
    src = f" — _{item['source']}_" if item["source"] else ""
    return f"• {prefix}[{item['title']}]({item['link']}){src} ({relative_time(item['pub_ts'])})"


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


def prune_seen(seen):
    cutoff = time.time() - SEEN_TTL_DAYS * 24 * 3600
    return {h: v for h, v in seen.items() if v.get("ts", 0) > cutoff}


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        print(text)
        return
    url = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    r = requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=20)
    if not r.ok:
        print(f"[ERROR] Telegram respondió {r.status_code}: {r.text}")


def main():
    state = load_state()
    seen = prune_seen(state.get("seen", {}))
    now = time.time()

    # ---- Descubrir candidatos (sin marcar como vistos todavía) ----
    asset_candidates = {}
    for symbol, label in ASSETS:
        query = f'"{label}" crypto'
        try:
            items = fetch_google_news(query, max_items=MAX_ITEMS_PER_ASSET)
        except Exception as e:
            print(f"[WARN] {label}: fallo al buscar noticias ({e})")
            continue
        found = 0
        for it in items:
            h = hash_link(it["link"])
            if h in seen:
                continue
            asset_candidates.setdefault(symbol, []).append((label, it, h))
            found += 1
        print(f"{symbol} ({label}): {found} noticia(s) nueva(s)")

    macro_candidates = []
    for query in MACRO_QUERIES:
        try:
            items = fetch_google_news(query, max_items=MAX_ITEMS_PER_MACRO_TOPIC)
        except Exception as e:
            print(f"[WARN] macro '{query}': fallo ({e})")
            continue
        for it in items:
            h = hash_link(it["link"])
            if h in seen:
                continue
            macro_candidates.append((it, h))
    print(f"Macro: {len(macro_candidates)} noticia(s) nueva(s)")

    total_candidates = len(macro_candidates) + sum(len(v) for v in asset_candidates.values())

    # ---- Siempre: registrar/resolver impacto, aunque no haya noticias nuevas ----
    impact_events = cni.load_impact_log()
    n_resolved = cni.resolve_open_events(impact_events)
    print(f"Impacto: {n_resolved} evento(s) resuelto(s) esta corrida.")

    if total_candidates == 0:
        print("Sin noticias nuevas en este ciclo — no se envía mensaje.")
        state["seen"] = seen
        save_state(state)
        cni.save_impact_log(impact_events)
        return

    # ---- Recortar al límite del mensaje, priorizando macro primero ----
    macro_to_send = macro_candidates[:MAX_ITEMS_PER_SECTION_IN_MSG]
    budget = MAX_TOTAL_ITEMS_PER_MESSAGE - len(macro_to_send)
    asset_to_send = {}
    for symbol, label in ASSETS:
        if symbol not in asset_candidates or budget <= 0:
            continue
        take = asset_candidates[symbol][:min(MAX_ITEMS_PER_SECTION_IN_MSG, budget)]
        if take:
            asset_to_send[symbol] = take
            budget -= len(take)

    sent_count = len(macro_to_send) + sum(len(v) for v in asset_to_send.values())
    pending_count = total_candidates - sent_count

    lines = [f"📰 *Resumen de noticias cripto* — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", ""]

    if macro_to_send:
        lines.append("*🌐 Macro cripto (regulación, ETFs, mercado general)*")
        for it, h in macro_to_send:
            lines.append(format_news_line(it))
        lines.append("")

    for symbol, label in ASSETS:
        if symbol not in asset_to_send:
            continue
        icon = "🟠" if symbol == "BTCUSDT" else "🔷"
        lines.append(f"*{icon} {label}*")
        for _, it, h in asset_to_send[symbol]:
            lines.append(format_news_line(it))
        lines.append("")

    if pending_count > 0:
        lines.append(f"_+{pending_count} noticias más, quedan para la próxima corrida._")
        lines.append("")

    lines.append("_Fuente: Google News. Sin evaluar relevancia. No es consejo financiero._")
    send_telegram("\n".join(lines))

    # ---- Marcar como vistas SOLO las enviadas ----
    for it, h in macro_to_send:
        seen[h] = {"ts": now}
    for entries in asset_to_send.values():
        for _, it, h in entries:
            seen[h] = {"ts": now}

    # ---- Registrar en el sistema de calibración (con la hora REAL de
    # publicación de cada noticia, no el momento de esta corrida) ----
    to_log = [
        {"symbol": "macro", "category": "macro", "title": it["title"], "link": it["link"], "published_at": it["pub_ts"]}
        for it, h in macro_to_send
    ]
    for symbol, entries in asset_to_send.items():
        for label, it, h in entries:
            to_log.append({
                "symbol": symbol, "category": symbol, "title": it["title"],
                "link": it["link"], "published_at": it["pub_ts"],
            })
    n_logged = cni.log_news_events(impact_events, to_log)
    print(f"Registrados {n_logged} evento(s) nuevo(s) en el sistema de calibración.")

    state["seen"] = seen
    save_state(state)
    cni.save_impact_log(impact_events)
    print(f"Resumen enviado: {sent_count} noticias. {pending_count} pendientes.")


if __name__ == "__main__":
    sys.exit(main())
