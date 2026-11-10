BITCOIN TELEGRAM MONITOR — CLOUD DEPLOYMENT

This package is prepared for a Render Background Worker.

IMPORTANT:
- The worker and persistent disk require a paid Render configuration.
- The persistent disk stores SQLite notification history across restarts/deploys.
- Never upload your real .env file or Telegram bot token to GitHub.
- Use a PRIVATE GitHub repository.
- Never provide a seed phrase, private key, or wallet password. The monitor only needs the public receiving address.

DEPLOY:
1. Create a PRIVATE GitHub repository.
2. Upload the contents of this folder (main.py, requirements.txt, render.yaml, .gitignore, .env.example).
3. In Render, choose New > Blueprint and connect the private repository.
4. Render reads render.yaml and proposes a Background Worker plus persistent disk.
5. During setup, provide BTC_ADDRESS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID when prompted.
6. Review the service and disk pricing before creating it. The persistent disk and always-on worker are paid resources.
7. Deploy. Check the Render Logs.
8. Confirm Telegram receives the online message and logs show successful API polling.

The service uses Frankfurt region as a reasonable starting point for connectivity from West Africa; it can be changed before creation if desired.

RUNTIME SETTINGS:
- POLL_SECONDS=15 means the service checks the address about every 15 seconds, plus API response time.
- DB_PATH=/var/data/seen_transactions.sqlite3 is on the persistent disk.
- Do not run a second copy of the same monitor against the same address/chat unless you intentionally want duplicate alerts.

LIMITATIONS:
- Polling can miss a transaction that appears and disappears between checks.
- An unconfirmed transaction can be replaced or dropped; it is not settled payment.
- The current version monitors one Bitcoin address only, not every derived address in a wallet.
- ETA is omitted because confirmation time cannot be reliably predicted.
