#!/usr/bin/env python3
"""
Crypto News Impact — módulo compartido para crypto_news_monitor.py
------------------------------------------------------------------------
Mide el efecto real de cada noticia sobre el precio, anclando SIEMPRE a la
hora real de publicación (no a la hora en que el bot detectó la noticia,
que puede ser varias horas después por correr solo 2x/día).

Precio de entrada/salida: velas históricas de Kraken, buscando la más
cercana al horario exacto de publicación (y 24hs después).

Benchmark: la OTRA moneda (BTC↔ETH), en la misma ventana exacta de
tiempo — no dependemos de ningún dato de "mercado cripto total", que
requeriría un plan pago de CoinGecko para tener historial.

Para noticias "macro" (sin moneda puntual), se mide el promedio de
movimiento de BTC y ETH en esa ventana — es la medida en sí misma, no
hay nada de qué aislarla.

El volumen de la vela de entrada se guarda (gratis, ya viene en la
respuesta de Kraken) pero TODAVÍA no se usa en ningún cálculo — se
revisará más adelante cuando haya suficiente historial acumulado.

NO ejecuta órdenes. NO es consejo financiero. Es diagnóstico.
"""

import os
import json
import time
import hashlib
import requests

IMPACT_LOG_FILE = os.environ.get("CRYPTO_NEWS_IMPACT_LOG_FILE", "crypto_news_impact_log.jsonl")
HORIZON_HOURS = float(os.environ.get("CRYPTO_NEWS_HORIZON_HOURS", 24))
ANCHOR_INTERVAL_MINUTES = int(os.environ.get("CRYPTO_NEWS_ANCHOR_INTERVAL_MIN", 15))

KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_PAIR_MAP = {"BTCUSDT": "XBTEUR", "ETHUSDT": "ETHEUR"}
OTHER_COIN = {"BTCUSDT": "ETHUSDT", "ETHUSDT": "BTCUSDT"}
COINS = ["BTCUSDT", "ETHUSDT"]

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

# Dirección esperada SOLO para las inequívocas.
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


# ---------- Precio anclado a un horario real (Kraken histórico) ----------
def fetch_price_context(symbol, target_ts, context_candles=25):
    """
    Busca en Kraken la vela más cercana a target_ts (timestamp Unix, en
    segundos) y devuelve su precio de cierre, el momento exacto de esa
    vela, y el ratio de volumen contra el promedio de las velas previas
    (guardado para más adelante, no se usa todavía).
    """
    pair = KRAKEN_PAIR_MAP.get(symbol)
    if not pair:
        return None

    since = int(target_ts) - ANCHOR_INTERVAL_MINUTES * 60 * (context_candles + 2)
    try:
        r = requests.get(
            KRAKEN_URL,
            params={"pair": pair, "interval": ANCHOR_INTERVAL_MINUTES, "since": since},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            print(f"[WARN] Kraken error para {symbol}: {data['error']}")
            return None
        result = data["result"]
        candles_key = next(k for k in result.keys() if k != "last")
        raw = result[candles_key]
        if not raw:
            return None
    except Exception as e:
        print(f"[WARN] No se pudo obtener histórico de {symbol} en Kraken: {e}")
        return None

    idx = min(range(len(raw)), key=lambda i: abs(raw[i][0] - target_ts))
    anchor = raw[idx]
    price = float(anchor[4])
    volume = float(anchor[6])

    prior = raw[max(0, idx - 20):idx]
    avg_volume = sum(float(row[6]) for row in prior) / len(prior) if prior else None
    volume_ratio = (volume / avg_volume) if avg_volume else None

    return {"price": price, "time": int(anchor[0]), "volume_ratio": volume_ratio}


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


# ---------- Registrar noticias nuevas ----------
def log_news_events(events, sent_items):
    """
    sent_items: cada dict trae symbol ('BTCUSDT'/'ETHUSDT'/'macro'),
    category, title, link, published_at (timestamp Unix real de la
    noticia, NO el momento de detección).
    """
    existing_ids = {e["id"] for e in events}
    added = 0
    for it in sent_items:
        eid = event_id(it["symbol"], it["link"])
        if eid in existing_ids:
            continue

        published_at = it["published_at"]
        entry_data = {}
        if it["symbol"] == "macro":
            for coin in COINS:
                ctx = fetch_price_context(coin, published_at)
                entry_data[coin] = ctx
        else:
            entry_data[it["symbol"]] = fetch_price_context(it["symbol"], published_at)

        events.append({
            "id": eid,
            "symbol": it["symbol"],
            "category": it["category"],
            "title": it["title"],
            "link": it["link"],
            "keywords": detect_keywords(it["title"]),
            "published_at": published_at,
            "horizon_hours": HORIZON_HOURS,
            "entry": entry_data,
            "status": "open",
            "resolved_at": None,
            "exit": None,
            "raw_pct_change": None,
            "benchmark_pct_change": None,
            "excess_pct_change": None,
            "market_pct_change": None,
        })
        added += 1
    return added


# ---------- Resolver (24hs reales después de la publicación) ----------
def resolve_open_events(events, now_ts=None):
    now_ts = now_ts or time.time()
    n_resolved = 0

    for e in events:
        if e["status"] != "open":
            continue
        resolve_at = e["published_at"] + e["horizon_hours"] * 3600
        if now_ts < resolve_at:
            continue  # todavía no pasó el horizonte real

        if e["symbol"] == "macro":
            exit_data = {}
            pct_changes = []
            ok = True
            for coin in COINS:
                entry_ctx = e["entry"].get(coin)
                if not entry_ctx:
                    ok = False
                    break
                exit_ctx = fetch_price_context(coin, resolve_at)
                if not exit_ctx:
                    ok = False
                    break
                exit_data[coin] = exit_ctx
                pct = (exit_ctx["price"] - entry_ctx["price"]) / entry_ctx["price"] * 100
                pct_changes.append(pct)
            if not ok:
                continue  # se reintenta la próxima corrida
            e["exit"] = exit_data
            e["market_pct_change"] = sum(pct_changes) / len(pct_changes)
            e["resolved_at"] = now_ts
            e["status"] = "closed"
            n_resolved += 1
            continue

        # Noticia de una moneda puntual
        symbol = e["symbol"]
        entry_ctx = e["entry"].get(symbol)
        if not entry_ctx:
            continue
        exit_ctx = fetch_price_context(symbol, resolve_at)
        if not exit_ctx:
            continue

        other = OTHER_COIN[symbol]
        other_entry = fetch_price_context(other, e["published_at"])
        other_exit = fetch_price_context(other, resolve_at)
        if not other_entry or not other_exit:
            continue

        raw_pct = (exit_ctx["price"] - entry_ctx["price"]) / entry_ctx["price"] * 100
        bench_pct = (other_exit["price"] - other_entry["price"]) / other_entry["price"] * 100

        e["exit"] = {symbol: exit_ctx, other: other_exit}
        e["raw_pct_change"] = raw_pct
        e["benchmark_pct_change"] = bench_pct
        e["excess_pct_change"] = raw_pct - bench_pct
        e["resolved_at"] = now_ts
        e["status"] = "closed"
        n_resolved += 1

    return n_resolved


# ---------- Calibración ----------
def _outcome_value(e):
    """El número que importa para calibrar: excess para noticias de una
    moneda puntual, market_pct_change (promedio BTC+ETH) para macro."""
    return e["excess_pct_change"] if e["symbol"] != "macro" else e["market_pct_change"]


def compute_calibration(events, min_samples=3):
    closed = [e for e in events if e["status"] == "closed"]
    coin_events = [e for e in closed if e["symbol"] != "macro"]
    macro_events = [e for e in closed if e["symbol"] == "macro"]

    def summarize(items, group_key_fn):
        buckets = {}
        for e in items:
            key = group_key_fn(e)
            if key is None:
                continue
            buckets.setdefault(key, []).append(e)
        out = {}
        for key, group in buckets.items():
            if len(group) < min_samples:
                continue
            values = [_outcome_value(g) for g in group]
            out[key] = {
                "n": len(values),
                "avg_abs": sum(abs(v) for v in values) / len(values),
                "avg_signed": sum(values) / len(values),
            }
        return out

    by_symbol = summarize(coin_events, lambda e: e["symbol"])

    # Palabras clave — SEPARADAS entre noticias de moneda puntual y macro,
    # para no mezclar dos métricas distintas (excess vs. market_pct_change).
    def keyword_summary(items):
        buckets = {}
        for e in items:
            for kw in e["keywords"]:
                buckets.setdefault(kw, []).append(e)
        out = {}
        for kw, group in buckets.items():
            if len(group) < min_samples:
                continue
            values = [_outcome_value(g) for g in group]
            entry = {
                "n": len(values),
                "avg_abs": sum(abs(v) for v in values) / len(values),
                "avg_signed": sum(values) / len(values),
            }
            expected = EXPECTED_DIRECTION.get(kw)
            if expected:
                decided = [v for v in values if v != 0]
                if decided:
                    hits = [v for v in decided if (expected == "up" and v > 0) or (expected == "down" and v < 0)]
                    entry["expected_direction"] = expected
                    entry["hit_rate"] = len(hits) / len(decided) * 100
            out[kw] = entry
        return out

    by_keyword_coin = keyword_summary(coin_events)
    by_keyword_macro = keyword_summary(macro_events)

    # Últimas noticias resueltas, para listar individualmente (no solo promedios)
    recent_resolved = sorted(closed, key=lambda e: e.get("resolved_at") or 0, reverse=True)[:10]

    return {
        "total_closed": len(closed),
        "total_open": len(events) - len(closed),
        "by_symbol": by_symbol,
        "by_keyword_coin": by_keyword_coin,
        "by_keyword_macro": by_keyword_macro,
        "recent_resolved": recent_resolved,
    }
