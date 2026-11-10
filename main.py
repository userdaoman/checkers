import logging
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

# Always load .env from the same folder as this file, not the terminal's
# current working directory.
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_FILE)

BTC_ADDRESS = os.getenv("BTC_ADDRESS", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = os.getenv("MEMPOOL_API_BASE", "https://mempool.space/api").rstrip("/")
POLL_SECONDS = max(10, int(os.getenv("POLL_SECONDS", "15")))
DB_PATH = os.getenv(
    "DB_PATH",
    str(Path(__file__).resolve().parent / "seen_transactions.sqlite3"),
)
SATOSHIS_PER_BTC = Decimal("100000000")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("btc-telegram-monitor")

session = requests.Session()
session.headers.update({"User-Agent": "btc-telegram-address-monitor/1.0"})


def check_configuration():
    missing = [
        name for name, value in (
            ("BTC_ADDRESS", BTC_ADDRESS),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing environment variable(s): "
            + ", ".join(missing)
            + f". Check your .env file at: {ENV_FILE}"
        )


def db_init():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS notified (
                txid TEXT PRIMARY KEY,
                first_seen INTEGER NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0
            )
        """)


def get_record(txid):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            "SELECT confirmed FROM notified WHERE txid = ?", (txid,)
        ).fetchone()


def save_record(txid, confirmed):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            INSERT INTO notified(txid, first_seen, confirmed)
            VALUES (?, ?, ?)
            ON CONFLICT(txid) DO UPDATE SET
                confirmed = MAX(notified.confirmed, excluded.confirmed)
            """,
            (txid, int(time.time()), int(confirmed)),
        )


def telegram_send(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = session.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=(10, 20),
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API returned an error: {result}")
    return result


def btc_amount_for_address(tx):
    total_sats = sum(
        int(output.get("value", 0))
        for output in tx.get("vout", [])
        if output.get("scriptpubkey_address") == BTC_ADDRESS
    )
    return Decimal(total_sats) / SATOSHIS_PER_BTC


def transaction_link(txid):
    return f"https://mempool.space/tx/{quote(txid, safe='')}"


def process_transaction(tx, initial_scan=False):
    txid = tx.get("txid")
    if not txid:
        return

    amount = btc_amount_for_address(tx)
    if amount <= 0:
        return

    status = tx.get("status") or {}
    confirmed_now = bool(status.get("confirmed"))
    existing = get_record(txid)
    link = transaction_link(txid)

    if confirmed_now:
        # On the very first run, silently baseline old confirmed history so
        # the bot does not send alerts for old payments.
        if existing is None and initial_scan:
            save_record(txid, confirmed=True)
            log.info("Baselined existing confirmed transaction %s", txid)
            return

        if existing is not None and int(existing[0]) == 1:
            return

        block_height = status.get("block_height", "unknown")
        telegram_send(
            "✅ BITCOIN PAYMENT CONFIRMED\n\n"
            f"Amount: {amount:.8f} BTC\n"
            "Status: Confirmed (at least 1 confirmation)\n"
            f"Block height: {block_height}\n"
            f"TXID: {txid}\n"
            f"View transaction: {link}"
        )
        save_record(txid, confirmed=True)
        log.info("Confirmation alert sent for %s", txid)
        return

    if existing is not None:
        return

    # Do not claim this is settled: a mempool transaction can be replaced
    # or dropped before it confirms.
    telegram_send(
        "🟠 INCOMING BITCOIN DETECTED\n\n"
        f"Amount: {amount:.8f} BTC\n"
        "Status: Unconfirmed (0 confirmations)\n"
        "ETA: Not reliably predictable\n\n"
        f"TXID: {txid}\n"
        f"View transaction: {link}\n\n"
        "An unconfirmed transaction is not final and may be delayed, "
        "replaced, or dropped."
    )
    save_record(txid, confirmed=False)
    log.info("Incoming transaction alert sent for %s (%s BTC)", txid, amount)


def fetch_address_transactions():
    # This endpoint returns the address's newest mempool transactions and
    # a recent slice of confirmed history. REST polling avoids the WebSocket
    # connection path that timed out on this computer/network.
    url = f"{API_BASE}/address/{quote(BTC_ADDRESS, safe='')}/txs"
    response = session.get(url, timeout=(10, 25))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected response from Mempool address API")
    return payload


def main():
    check_configuration()
    db_init()

    # Verify Telegram before starting the monitor.
    telegram_send(
        "🟢 Bitcoin monitor is online.\n"
        "Monitoring your configured public Bitcoin address using REST polling.\n"
        f"Check interval: {POLL_SECONDS} seconds."
    )

    log.info("Starting REST monitor; API base: %s", API_BASE)
    log.info("Polling interval: %s seconds", POLL_SECONDS)

    initial_scan = True
    retry_delay = POLL_SECONDS

    while True:
        try:
            transactions = fetch_address_transactions()

            # Process oldest-to-newest so notifications arrive in a sensible
            # order when multiple transactions are returned together.
            for tx in reversed(transactions):
                process_transaction(tx, initial_scan=initial_scan)

            initial_scan = False
            retry_delay = POLL_SECONDS
            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            log.info("Stopping Bitcoin monitor")
            break
        except requests.HTTPError as exc:
            status_code = (
                exc.response.status_code if exc.response is not None else "unknown"
            )
            log.exception("Mempool API HTTP error (status %s)", status_code)
            if status_code == 429:
                retry_delay = min(max(retry_delay * 2, 30), 300)
            else:
                retry_delay = min(max(retry_delay * 2, POLL_SECONDS), 300)
            log.warning("Retrying in %s seconds", retry_delay)
            time.sleep(retry_delay)
        except (requests.RequestException, ValueError, RuntimeError):
            log.exception("Monitoring/API error")
            retry_delay = min(max(retry_delay * 2, POLL_SECONDS), 300)
            log.warning("Retrying in %s seconds", retry_delay)
            time.sleep(retry_delay)
        except Exception:
            log.exception("Unexpected monitor error")
            retry_delay = min(max(retry_delay * 2, POLL_SECONDS), 300)
            time.sleep(retry_delay)


if __name__ == "__main__":
    main()
