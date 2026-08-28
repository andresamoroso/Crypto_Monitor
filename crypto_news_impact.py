#!/usr/bin/env python3
"""
Crypto News Impact — módulo compartido para crypto_news_monitor.py
------------------------------------------------------------------------
Registra cada noticia de cripto mostrada, con el precio de la moneda Y el
market cap total del mercado cripto en ese momento (vía CoinGecko, gratis,
sin API key) — el equivalente cripto al "SPY" que usamos para acciones.

Un tiempo después (por defecto, 2 corridas de este script — con la
frecuencia de 2x/día, son ~24hs), se resuelve: cuánto se movió la moneda
vs. cuánto se movió el mercado cripto en general, para aislar el efecto
propio de la noticia.

NO ejecuta órdenes. NO es consejo financiero. Es diagnóstico, no un
piloto automático.
"""

import os
import json
import time
import hashlib
import requests

IMPACT_LOG_FILE = os.environ.get("CRYPTO_NEWS_IMPACT_LOG_FILE", "crypto_news_impact_log.jsonl")
HORIZON_RUNS = int(os.environ.get("CRYPTO_NEWS_HORIZON_RUNS", 2))  # ~24hs con 2 corridas/día

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"  # sin usar; se deja por referencia
COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
COINGECKO_ID_MAP = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}

# ---------- Palabras clave relevantes para noticias de cripto ----------
EVENT_KEYWORDS = {
    "hack": ["hack", "exploit", "breach", "stolen", "drained"],
    "ban": ["ban", "banned", "crackdown"],
    "lawsuit": ["lawsuit", "sues", "sued", "charged"],
    "etf_approval": ["etf approved", "etf approval", "approves bitcoin etf", "approves ether etf", "approves ethereum etf"],
    "etf_rejection": ["etf rejected", "etf denied", "rejects bitcoin etf"],
    "delisting": ["delisted", "delisting"],
    "listing": ["listed on", "new listing"],
    "partnership": ["partnership", "partners with", "adoption"],
    "upgrade": ["hard fork", "mainnet", "upgrade"],
    "regulation": ["regulation", "regulator", " sec "],
}

# Dirección esperada SOLO para las inequívocas — igual criterio que con
# acciones: "upgrade" y "regulation" genérica quedan afuera a propósito,
# pueden ser buena o mala noticia según el contenido real.
EXPECTED_DIRECTION = {
    "hack": "down",
    "ban": "down",
    "lawsuit": "down",
    "etf_approval": "up",
    "etf_rejection": "down",
    "delisting": "down",
    "listing": "up",
    "partnership": "up",
}


def detect_keywords(title):
    title_low = f" {title.lower()} "
    return [tag for tag, subs in EVENT_KEYWORDS.items() if any(s in title_low for s in subs)]


# ---------- Precio y benchmark en vivo (un solo punto, no una serie) ----------
def fetch_price(symbol):
    coin_id = COINGECKO_ID_MAP.get(symbol)
    if not coin_id:
        print(f"[WARN] Símbolo desconocido para CoinGecko: {symbol}")
        return None
    try:
        r = requests.get(
            COINGECKO_SIMPLE_PRICE_URL,
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=15,
        )
        r.raise_for_status()
        return float(r.json()[coin_id]["usd"])
    except Exception as e:
        print(f"[WARN] No se pudo obtener precio de {symbol} vía CoinGecko: {e}")
        return None


def fetch_global_market_cap():
    try:
        r = requests.get(COINGECKO_GLOBAL_URL, timeout=15)
        r.raise_for_status()
        return float(r.json()["data"]["total_market_cap"]["usd"])
    except Exception as e:
        print(f"[WARN] No se pudo obtener el market cap global: {e}")
        return None


# ---------- Persistencia ----------
def load_impact_log():
    if not os.path.exists(IMPACT_LOG_FILE):
        return []
    events = []
    with open(IMPACT_LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def save_impact_log(events):
    with open(IMPACT_LOG_FILE, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def event_id(symbol, link):
    return hashlib.sha1(f"{symbol}|{link}".encode("utf-8")).hexdigest()[:16]


# ---------- Registrar noticias nuevas (con precio y market cap del momento) ----------
def log_news_events(events, sent_items, market_cap_now):
    existing_ids = {e["id"] for e in events}
    added = 0
    for it in sent_items:
        eid = event_id(it["symbol"], it["link"])
        if eid in existing_ids:
            continue
        entry_price = fetch_price(it["symbol"]) if it["symbol"] != "macro" else None
        events.append({
            "id": eid, "symbol": it["symbol"], "category": it["category"],
            "title": it["title"], "link": it["link"],
            "keywords": detect_keywords(it["title"]),
            "detected_at": time.time(),
            "entry_price": entry_price,
            "entry_market_cap": market_cap_now,
            "runs_elapsed": 0, "horizon_runs": HORIZON_RUNS,
            "status": "open" if entry_price is not None or it["symbol"] == "macro" else "skipped_no_price",
            "resolved_at": None, "exit_price": None, "exit_market_cap": None,
            "raw_pct_change": None, "benchmark_pct_change": None, "excess_pct_change": None,
        })
        added += 1
    return added


def resolve_open_events(events, market_cap_now):
    n_resolved = 0
    for e in events:
        if e["status"] != "open":
            continue
        e["runs_elapsed"] += 1
        if e["runs_elapsed"] < e["horizon_runs"]:
            continue
        if e["symbol"] == "macro":
            # Las noticias macro (sin moneda puntual) solo se comparan
            # contra el propio mercado, no tienen "precio propio".
            if e["entry_market_cap"] and market_cap_now:
                bench_pct = (market_cap_now - e["entry_market_cap"]) / e["entry_market_cap"] * 100
                e["raw_pct_change"] = bench_pct
                e["benchmark_pct_change"] = bench_pct
                e["excess_pct_change"] = 0.0
                e["exit_market_cap"] = market_cap_now
                e["resolved_at"] = time.time()
                e["status"] = "closed"
                n_resolved += 1
            continue

        exit_price = fetch_price(e["symbol"])
        if exit_price is None or market_cap_now is None:
            continue  # se reintenta la próxima corrida
        raw_pct = (exit_price - e["entry_price"]) / e["entry_price"] * 100
        bench_pct = (market_cap_now - e["entry_market_cap"]) / e["entry_market_cap"] * 100
        e["exit_price"] = exit_price
        e["exit_market_cap"] = market_cap_now
        e["raw_pct_change"] = raw_pct
        e["benchmark_pct_change"] = bench_pct
        e["excess_pct_change"] = raw_pct - bench_pct
        e["resolved_at"] = time.time()
        e["status"] = "closed"
        n_resolved += 1
    return n_resolved


# ---------- Calibración ----------
def compute_calibration(events, min_samples=3):
    closed = [e for e in events if e["status"] == "closed"]

    def summarize(group_key_fn):
        buckets = {}
        for e in closed:
            key = group_key_fn(e)
            if key is None:
                continue
            buckets.setdefault(key, []).append(e)
        out = {}
        for key, items in buckets.items():
            if len(items) < min_samples:
                continue
            values = [it["excess_pct_change"] for it in items]
            out[key] = {
                "n": len(values),
                "avg_abs_excess": sum(abs(v) for v in values) / len(values),
                "avg_signed_excess": sum(values) / len(values),
            }
        return out

    by_symbol = summarize(lambda e: e["symbol"])

    by_keyword = {}
    for e in closed:
        for kw in e["keywords"]:
            by_keyword.setdefault(kw, []).append(e)
    by_keyword_summary = {}
    for kw, items in by_keyword.items():
        if len(items) < min_samples:
            continue
        values = [it["excess_pct_change"] for it in items]
        entry = {
            "n": len(values),
            "avg_abs_excess": sum(abs(v) for v in values) / len(values),
            "avg_signed_excess": sum(values) / len(values),
        }
        expected = EXPECTED_DIRECTION.get(kw)
        if expected:
            decided = [v for v in values if v != 0]
            if decided:
                hits = [v for v in decided if (expected == "up" and v > 0) or (expected == "down" and v < 0)]
                entry["expected_direction"] = expected
                entry["hit_rate"] = len(hits) / len(decided) * 100
        by_keyword_summary[kw] = entry

    return {
        "total_closed": len(closed),
        "total_open": len(events) - len(closed),
        "by_symbol": by_symbol,
        "by_keyword": by_keyword_summary,
    }
