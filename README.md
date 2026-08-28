# Crypto Monitor — guía completa

Sistema watch-only que vigila BTC y ETH, detecta zonas de giro y cambios
de tendencia, rastrea noticias relacionadas, y mide qué tan seguido
acierta todo esto — nada ejecuta órdenes, nada es consejo financiero.

Corre 100% gratis en GitHub Actions. Cotizaciones en **euros**, vía
Kraken (no Binance — bloquea por región desde los runners de GitHub) y
CoinGecko donde corresponde.

---

## Los 5 programas de este repo

| Archivo | Qué hace | Cuándo corre |
|---|---|---|
| `crypto_monitor.py` | Detecta zonas de giro (15m) + estado de tendencia (4h/1d), con paper-testing de cada alerta | Cada 5 minutos |
| `crypto_checkpoint_report.py` | Win rate por tier, por moneda, y cruce con noticias cercanas | Días 1 y 15 de cada mes (+ a demanda) |
| `crypto_news_monitor.py` | Busca noticias de BTC/ETH + temas macro cripto, sin juzgar relevancia | 2x/día |
| `crypto_news_impact.py` | Módulo compartido: mide el efecto real de cada noticia sobre el precio | Se ejecuta *dentro* de crypto_news_monitor.py |
| `crypto_news_calibration_report.py` | Qué tipo de noticia realmente movió el precio, con listado individual | Lunes (+ a demanda) |

Workflows en `.github/workflows/`: `monitor.yml`, `checkpoint.yml`,
`crypto_news.yml`, `crypto_news_calibration.yml`.

---

## 1. Detección de señales — dos sistemas separados

**Sistema 1 — Zona de giro (15m):** RSI(14) en extremo + precio tocando
banda de Bollinger + volumen ≥1.5x el promedio + vela en la dirección
correcta. Busca el momento exacto de un posible rebote.

**Sistema 2 — Estado de tendencia (4h + 1d):** cruce de medias 9/21 +
momentum ajustado por volatilidad (ATR) + confirmación diaria. Es un
estado permanente, no una alerta puntual — avisa cuando *cambia*
(alcista → bajista o viceversa).

**Cómo se combinan:** cada alerta de giro se etiqueta según la tendencia
vigente en ese momento:
- ✅ **Fuerte** — el giro y la tendencia mayor coinciden
- ➖ **Moderada** — tendencia neutral, sin oponerse
- ⚠️ **Contraria** — el giro va contra la tendencia mayor (más riesgo)

Cada alerta se registra en `signals_log.jsonl` con precio de entrada,
target/stop calculados con ATR (volatilidad real), y se resuelve sola
unas horas después (target/stop/timeout).

---

## 2. Checkpoint de señales técnicas

Te dice, en lenguaje simple: win rate general, por tier, por moneda —
**y cruza cada alerta contra el registro de noticias**: ¿las alertas que
tuvieron una noticia real publicada cerca (±6hs) acertaron más que las
que aparecieron solas? Esa es la pregunta clave que este checkpoint
responde con datos, no con promedios generales.

---

## 3. Noticias de cripto

2 veces al día, Google News para Bitcoin, Ethereum, y 4 temas macro
(regulación, ETFs, mercado general, SEC). Sin filtrar por relevancia —
titulares tal cual, agrupados, sin repetir lo ya visto. Si hay demasiadas
de golpe, prioriza y deja el resto en cola para la corrida siguiente.

---

## 4. Calibración de impacto de noticias — el diseño técnico

La parte más cuidada del proyecto. Para cada noticia:

1. **Precio de entrada anclado a la hora REAL de publicación** (no al
   momento en que el bot la detectó, que puede ser horas después) — se
   busca en el histórico de Kraken la vela más cercana a ese horario
   exacto.
2. **Benchmark: la otra moneda.** Para una noticia de Bitcoin, se
   compara el movimiento de BTC contra el de ETH en la misma ventana
   exacta — así se aísla el "efecto propio" de la noticia del
   movimiento general del mercado. (No usamos el market cap total del
   mercado cripto: ese historial requiere un plan pago de CoinGecko.)
3. **Noticias macro** (sin moneda puntual): se mide el promedio de
   movimiento de BTC y ETH — es la medida en sí misma, no hay nada que
   aislar.
4. **Horizonte: 24hs reales** desde la publicación (no "corridas
   transcurridas" — tiempo de reloj real).
5. **Volumen de la vela de entrada**: se guarda (viene gratis en la
   misma consulta a Kraken) pero **todavía no se usa** en ningún
   cálculo — queda pendiente de revisión una vez que haya más historial
   acumulado (40-50 noticias cerradas).

El reporte de calibración muestra promedios por moneda y por palabra
clave (`hack`, `etf_approval`, `regulation`, etc.) — **separando
siempre** noticias de moneda puntual de noticias macro, para no mezclar
dos métricas distintas — y una lista de las últimas noticias resueltas
con su resultado individual, no solo promedios.

Para 7 palabras clave con dirección inequívoca (`hack`, `ban`,
`lawsuit`, `etf_approval`, `etf_rejection`, `delisting`, `listing`,
`partnership`) también se muestra el % de veces que acertó la dirección
esperada. El resto (`upgrade`, `regulation`, `rate_decision`) queda sin
esa métrica a propósito — su dirección real depende del contenido, no
se puede asumir.

No se muestra nada con menos de 3 casos acumulados.

---

## Instalación / actualización

Mismo proceso para cualquier archivo: abrir en GitHub → lápiz (editar) →
seleccionar todo → borrar → pegar el contenido nuevo → Commit. Los
`.yml` van siempre en `.github/workflows/nombre.yml`.

Secrets usados (ya configurados): `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`.

## Probar cualquier programa a mano

Pestaña **Actions** → elegí el workflow → **Run workflow**. Los 4
workflows se pueden disparar así en cualquier momento.

**Ojo con la concurrencia:** como `monitor.yml` corre cada 5 minutos y
escribe en el repo, si lo disparás a mano justo cuando también está
por correr la versión automática, puede aparecer un error de Git por
conflicto (`CONFLICT modify/delete`). No es un bug — es una carrera
puntual entre dos corridas simultáneas. Se resuelve solo: esperá un
minuto y volvé a correrlo.

## Variables de configuración (todas opcionales, con default razonable)

| Variable | Afecta a | Default |
|---|---|---|
| `RSI_PERIOD`, `RSI_LOW`, `RSI_HIGH` | Sistema de giro | 14 / 30 / 70 |
| `BB_PERIOD`, `BB_STD` | Bandas de Bollinger | 20 / 2.0 |
| `VOLUME_MULTIPLIER` | Umbral de volumen para el giro | 1.5 |
| `MA_FAST`, `MA_SLOW` | Cruce de medias (tendencia) | 9 / 21 |
| `MOMENTUM_ATR_MULTIPLIER` | Sensibilidad del momentum (tendencia) | 1.5 |
| `HORIZON_CANDLES` | Velas de 15m para resolver una alerta de giro | 4 |
| `CRYPTO_NEWS_HORIZON_HOURS` | Horas reales para resolver el impacto de una noticia | 24 |
| `NEWS_CORRELATION_WINDOW_HOURS` | Ventana para cruzar alertas con noticias | 6 |
| `MAX_TOTAL_ITEMS_PER_MESSAGE` | Tope de noticias por mensaje de Telegram | 30 |
| `MIN_SAMPLES_FOR_FLAGS` / `CRYPTO_CALIBRATION_MIN_SAMPLES` | Mínimo de casos antes de mostrar una estadística | 3-8 |
