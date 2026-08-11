# Crypto Signal Monitor — guía de instalación

Bot watch-only que corre 24/7 en la nube (gratis, con GitHub Actions) y te
avisa por Telegram cuando RSI + cruce de medias + momentum se alinean en
BTC o ETH. No ejecuta órdenes. No es consejo financiero.

---

## Paso 1 — Crear el bot de Telegram (2 min)

1. Abrí Telegram y buscá **@BotFather**.
2. Enviale `/newbot` y seguí las instrucciones (nombre, username terminado en `bot`).
3. BotFather te va a dar un **token** con esta pinta:
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
   Guardalo, es tu `TELEGRAM_BOT_TOKEN`.

## Paso 2 — Conseguir tu Chat ID

1. Buscá tu bot recién creado en Telegram (por su username) y enviale cualquier mensaje, ej. "hola".
2. En el navegador, andá a (reemplazando `<TOKEN>` por el tuyo):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Vas a ver un JSON. Buscá `"chat":{"id":XXXXXXXXX` — ese número es tu
   `TELEGRAM_CHAT_ID`.

## Paso 3 — Subir este proyecto a GitHub

1. Creá un repo nuevo en GitHub (puede ser **privado**).
2. Subí esta carpeta completa (`crypto_monitor.py`, `requirements.txt`,
   `.github/workflows/monitor.yml`, este `README.md`).
   - Más fácil: arrastrá los archivos en la interfaz web de GitHub
     ("Add file" → "Upload files"), o usá `git push` si ya conocés Git.

## Paso 4 — Configurar los secretos

En tu repo de GitHub: **Settings → Secrets and variables → Actions → New repository secret**

Creá dos secretos:
- `TELEGRAM_BOT_TOKEN` → el token del Paso 1
- `TELEGRAM_CHAT_ID` → el número del Paso 2

## Paso 5 — Listo, ya está corriendo

El workflow (`.github/workflows/monitor.yml`) se ejecuta solo **cada 15
minutos**, para siempre, aunque tengas la compu apagada. Podés:

- Ir a la pestaña **Actions** de tu repo para ver los logs de cada corrida.
- Tirarlo a mano con el botón **"Run workflow"** ahí mismo, para probar que
  todo funciona antes de esperar al cron.

Cuando 2 o más señales se alineen en BTC o ETH, te va a llegar un mensaje
como este a Telegram:

```
🟢 BTC — posible rebote (señales alcistas alineadas)
Precio: $61,234.50
RSI(14): 27.3
Medias 9/21: bull_cross
Momentum: +1.85%
Timeframe: 1h
```

No te va a repetir la misma alerta en cada corrida — solo avisa cuando la
dirección de la señal *cambia* (de neutral a bull, de bull a bear, etc.),
gracias al archivo `state.json` que se guarda entre corridas.

---

## Ajustar la sensibilidad

Todos los parámetros están como variables de entorno en
`.github/workflows/monitor.yml`, en el paso `Run monitor`:

| Variable     | Qué hace                              | Default |
|--------------|----------------------------------------|---------|
| `INTERVAL`   | Timeframe de las velas (15m, 1h, 4h, 1d) | `1h`  |
| `RSI_PERIOD` | Período del RSI                        | `14`    |
| `MA_FAST`    | Media móvil rápida                     | `9`     |
| `MA_SLOW`    | Media móvil lenta                      | `21`    |
| `RSI_LOW`    | Umbral de sobreventa                   | `30`    |
| `RSI_HIGH`   | Umbral de sobrecompra                  | `70`    |

Para cambiar la frecuencia de chequeo, editá la línea `cron` en el mismo
archivo (formato: minuto hora día mes día-semana, en UTC). Por ejemplo,
`*/5 * * * *` = cada 5 minutos. **Ojo:** en repos públicos las GitHub
Actions son gratis sin límite práctico; en repos privados tenés ~2000
minutos gratis por mes, así que si lo hacés muy frecuente en un repo
privado podés gastar la cuota.

## Probarlo en tu compu antes de subirlo (opcional)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="tu_token"
export TELEGRAM_CHAT_ID="tu_chat_id"
python crypto_monitor.py
```

Si no configurás las variables de Telegram, el script igual corre y te
muestra el resultado por consola (útil para probar la lógica sin spamear
tu Telegram).

## Qué NO hace este bot

- No compra ni vende nada.
- No garantiza resultados ni interpreta noticias/fundamentales.
- No reemplaza tu criterio — es un vigía que te ahorra estar mirando la
  pantalla todo el día.
