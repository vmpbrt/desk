# Desk

Private 5-agent Solana desk (Scanner → Auditor → Analyst → Executor → Risk). It never places orders: every buy and sell is a Telegram push with a FOMO button.

* `desk/`: one asyncio process (`python -m desk.main`)
* `web/`: static iPhone app (plain HTML/CSS/JS, canvas charts)
* `scripts/`: start/stop/watchdog and the git askpass helper (reads the token from the environment only)
* `tests/`: pytest

Hosting (plan B): `main` holds the code. The orphan `gh-pages` branch holds the app plus `data/*.bin`, force-pushed as a single commit. Every data file is AES-256-GCM encrypted with a key that is never in this repo, and file names are HMAC-opaque. Without the key the app shows "Locked".

Control: Telegram (`help`). The app is read-only.
