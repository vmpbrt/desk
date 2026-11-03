# Deviations and assumptions (please confirm)

## Confirmed deviations (from the owner)
- Hosting is plan B: GitHub Pages from the public repo vmpbrt/desk. Code is on `main`; app and data are on the orphan `gh-pages` branch, force-pushed as one commit.
- Every data file is AES-256-GCM encrypted. The key is in `~/.desk_secrets/data_key` (600) and never in the repo. File names are HMAC-SHA256(key, name)[:32].bin.
- The app opens once with `#d=<key>`. The key goes into localStorage, the fragment is stripped, and without the key the app shows "Locked".
- Analyst runs in `rules` mode (no Jev key). Jev mode is implemented but unused.
- Notifier is Telegram (owner chat from owner.json). There is no web push in plan B.
- Settings, pause, and mode changes happen through Telegram commands. The Account tab is read-only.
- Top 10 holders are computed from getProgramAccounts (Token + Token-2022), grouped by owner. Accounts owned by the pool and the bonding-curve PDA are excluded. The results are cached for 45s and shared by Scanner L4 and Auditor L4/L8.
- Pool = the DexScreener pumpswap pair with the highest liquidity. Creator = RugCheck `creator`.
- Layer 5 uses RugCheck `markets[pubkey==pool].lp.lpLockedPct`. There is no "burned" key, so the value reads "N% locked".
- Layer 7 uses the graph `links` key. An empty `[]` graph = "None" (pass). A network with no `links` key = red "no data: bundles".

## Builder assumptions (Parts 1 and 3 of the master prompt were not available to the builder)
1. **Push cadence.** Data is written locally every 15s. It is pushed to Pages every 360s by default, not every 60s. Branch-built Pages has a documented soft limit of 10 builds/hour, and 60s pushes would be 60/hour. The setting is `set publish.push_s 60` (allowed range 60–3600). The app shows "published Xm ago".
2. **Night.** A night runs 12:00 ET to 12:00 ET. Rules like "once per night" and "tonight" counts use this window.
3. **Fees.** 0.45% per side on both the buy and sell notional (0.50% minus the referral discount). Gas is $0. Setting: `general.fee_pct`.
4. **Min ticket.** $47.50. With `executor.below_min=bump`, a size below the minimum is raised to $47.50 when free cash covers it; otherwise the trade is skipped. A size above free cash is skipped as "no free stack".
5. **LIVE mode.** `mode live`, then type `LIVE` within 2 minutes. Mode can't change while a position is open.
   - In LIVE, a Buy push carries a [Bought] button. If it isn't tapped within `executor.live_tap_min` (5) minutes, the trade is logged as "missed".
   - A Sell push carries a [Sold] button and is re-sent once after `risk.resend_min` minutes.
6. **Telegram command set:** help, status, why <coin>, pause/resume <agent>, stack <usd>, mode paper|live, set <group.key> <value>, settings [group], changes, rpc <url>, test, report.
7. **Config ranges.** These are my own picks; see `desk/config.py` RANGES.
8. **Morning report.** At 08:00 ET it is written to `reports/morning-<night>.md` and pushed to Telegram. The layer-8 kill count goes on top if layer 8 had no data.
9. **Layer 7 grouping.** Groups are connected components over all graph nodes (funders included). Size = number of holder nodes (holdings > 0) in the largest component. Red at ≥ `auditor.max_bundle` (5).
10. **Layer 8.** Red if a single delegate controls ≥ `auditor.max_delegate` (10) holder accounts.
11. **STEP 8 colors and fonts.** iOS system palette (#000 / #1c1c1e / #30d158 / #ff453a / #ff9f0a / #0a84ff) and the SF system font stack.
12. **Chart ranges.** 1H / 6H / 24H / ALL.
13. **Coin pages.** Published for coins seen in the last day that reached the Auditor or a position (60 coins max).
