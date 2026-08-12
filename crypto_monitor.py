name: Crypto Signal Monitor

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch: {}

permissions:
  contents: write   # necesario para que el bot pueda commitear el registro de señales

jobs:
  check-signals:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run monitor
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          INTERVAL: '15m'
          RSI_PERIOD: '14'
          MA_FAST: '9'
          MA_SLOW: '21'
          RSI_LOW: '30'
          RSI_HIGH: '70'
          HTF_INTERVAL: '4h'
          HTF_SMA_PERIOD: '50'
          REQUIRE_VOLUME: 'true'
          REQUIRE_HTF_TREND: 'true'
          TARGET_PCT: '1.5'
          STOP_PCT: '1.0'
          HORIZON_CANDLES: '4'
          NOTIFY_ON_RESOLUTION: 'true'
        run: python crypto_monitor.py

      - name: Guardar estado y registro de señales en el repo
        run: |
          git config user.name "crypto-signal-bot"
          git config user.email "actions@github.com"
          git add state.json signals_log.jsonl
          if git diff --cached --quiet; then
            echo "Sin cambios para commitear."
          else
            git commit -m "Actualiza estado y registro de señales [skip ci]"
            git pull --rebase
            git push
          fi
