"""AGENT 1 · SCANNER. Counts and cuts. Never judges quality."""
import asyncio, json, re, time, unicodedata
import websockets
from . import fields as F
from .core import iso, night_id, money
from .sources import RateLimited

WS_URL = "wss://pumpportal.fun/api/data"


def norm_name(s):
    s = (s or "").lower()
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith(("S", "C", "Z")))  # emoji/space/control
    return re.sub(r"[\s\d]", "", s)


def is_copycat(name, symbol, runners):
    """runners: list of (mint, name, symbol). Exact match or one inside the other."""
    cands = [norm_name(name), norm_name(symbol)]
    for _, rn, rs in runners:
        for r in (norm_name(rn), norm_name(rs)):
            if not r:
                continue
            for c in cands:
                if c and (c == r or c in r or r in c):
                    return rn or rs
    return None


class Scanner:
    name = "scanner"

    def __init__(self, desk):
        self.d = desk
        self.L = desk.log("scanner")
        self.last_msg = time.time()
        self.passed_tonight = set()
        self.reconnects = 0

    def card(self, **kw):
        self.d.live.setdefault("scanner", {"feed": []}).update(kw)

    # ---------- input: websocket ----------
    async def ws_loop(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    self.L.info(f"{iso()} ws connected")
                    backoff = 1
                    self.last_msg = time.time()
                    while True:
                        silence = self.d.s["scanner"]["ws_silence_s"]
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=silence)
                        except asyncio.TimeoutError:
                            raise ConnectionError(f"silent {silence}s")
                        self.last_msg = time.time()
                        msg = json.loads(raw)
                        if F.get(msg, F.MIGRATION["mint"]):
                            await self.on_migration(msg)
            except Exception as e:  # noqa
                self.reconnects += 1
                self.L.info(f"{iso()} ws reconnect #{self.reconnects} in {backoff}s: {type(e).__name__} {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def on_migration(self, msg):
        mint = F.get(msg, F.MIGRATION["mint"])
        db = self.d.db
        if db.one("SELECT 1 FROM coins WHERE mint=?", mint):
            return
        now = time.time()
        db.x("INSERT INTO coins(mint,signature,migrated_at,first_seen,status,creator) VALUES(?,?,?,?, 'waiting', ?)",
             mint, F.get(msg, F.MIGRATION["signature"]), now, now, F.get(msg, F.MIGRATION["creator"]))
        asyncio.create_task(self.fill_creator(mint))

    async def fill_creator(self, mint):
        """creator: migration message (never present) -> RugCheck report. Store creator + balance NOW."""
        db = self.d.db
        creator = (db.one("SELECT creator FROM coins WHERE mint=?", mint) or {}).get("creator")
        bal = None
        for attempt in range(3):
            try:
                if not creator:
                    rep = await self.d.src.rug(mint, "report")
                    creator = F.get(rep, F.RUGCHECK_REPORT["creator"])
                    rb = F.get(rep, F.RUGCHECK_REPORT["creatorBalance"])
                    bal = int(rb) if rb is not None else None
                if creator:
                    try:
                        bal = await self.d.src.token_balance(creator, mint)
                    except Exception:
                        pass
                break
            except Exception:
                await asyncio.sleep(5)
        db.x("UPDATE coins SET creator=?, creator_bal_raw=? WHERE mint=?",
             creator, str(bal) if bal is not None else None, mint)

    # ---------- the waiting room ----------
    def runners(self):
        rows = self.d.db.q("SELECT mint,name,symbol FROM coins WHERE h24_max>=? AND first_seen>=?",
                           self.d.s["scanner"]["runner_h24_pct"], time.time() - 86400)
        return [(r["mint"], r["name"], r["symbol"]) for r in rows]

    async def poll_loop(self):
        while True:
            s = self.d.s["scanner"]
            if not self.d.db.paused("scanner"):
                try:
                    await self.poll_once()
                except RateLimited:
                    self.L.info(f"{iso()} dexscreener 429, waiting {s['dex_429_wait_s']}s, room kept")
                    await asyncio.sleep(s["dex_429_wait_s"])
                except Exception as e:  # noqa
                    self.L.info(f"{iso()} poll error {type(e).__name__}: {e}")
            self.d.db.heartbeat("scanner", f"room={len(self.room())} reconnects={self.reconnects}")
            await asyncio.sleep(s["poll_s"])

    def room(self):
        return self.d.db.q("SELECT * FROM coins WHERE status='waiting'")

    async def poll_once(self):
        db, s = self.d.db, self.d.s["scanner"]
        room = self.room()
        if not room:
            return
        pairs = await self.d.src.dex_tokens([c["mint"] for c in room])
        for c in room:
            await self.judge(c, pairs.get(c["mint"], []), s)

    async def judge(self, c, pairs, s):
        db, mint, now = self.d.db, c["mint"], time.time()
        for p in pairs:  # track runners on any pair
            h24 = F.get(p, F.DEXSCREENER["priceChange_h24"])
            if h24 is not None:
                db.x("UPDATE coins SET h24_max=MAX(COALESCE(h24_max,-1e9),?) WHERE mint=?", h24, mint)
        pair = self.d.src.pumpswap_pair(pairs)
        if pair is None:  # DexScreener doesn't know the PumpSwap pool yet: it waits
            if now - c["first_seen"] > s["max_wait_min"] * 60:
                self.decide(c, None, "kill", "never qualified", {})
            return
        self.d.last_pair[mint] = pair
        name, sym = F.get(pair, F.DEXSCREENER["name"]), F.get(pair, F.DEXSCREENER["symbol"])
        pool = F.get(pair, F.DEXSCREENER["pairAddress"])
        price = F.get(pair, F.DEXSCREENER["priceUsd"])
        db.x("UPDATE coins SET name=?, symbol=?, pool=? WHERE mint=?", name, sym, pool, mint)
        if price is not None:
            db.price(mint, float(price), F.get(pair, F.DEXSCREENER["volume_m5"]))
        c.update(name=name, symbol=sym, pool=pool)
        liq = F.get(pair, F.DEXSCREENER["liquidityUsd"]) or 0
        h1 = F.get(pair, F.DEXSCREENER["priceChange_h1"]) or 0
        created = F.get(pair, F.DEXSCREENER["pairCreatedAt"])
        age_min = int((now - created / 1000) / 60) if created else 0
        m = {"liq": liq, "h1": h1, "age": age_min, "top10": None}
        if mint in self.passed_tonight:
            return
        if liq < s["min_liq_usd"]:
            reason = f"liq ${money(liq)}"
        else:
            reason = None
            try:
                top10 = await self.top10(mint, pool)
                m["top10"] = top10
                if top10 is not None and top10 > s["max_top10_pct"]:
                    reason = f"top 10 hold {top10:.0f}%"
            except Exception as e:  # noqa
                self.L.info(f"{iso()} {mint} top10 fetch failed: {e} (waits)")
                top10 = None
            if not reason and h1 > s["max_h1_pct"]:
                reason = f"+{h1:.0f}% in 1h"
            if not reason:
                cc = is_copycat(name, sym, [r for r in self.runners() if r[0] != mint])
                if cc:
                    reason = f"copycat of {cc}"
            if not reason and age_min >= s["min_age_min"] and m["top10"] is not None:
                return self.decide(c, pair, "pass", None, m)
        if reason:
            return self.decide(c, pair, "kill", reason, m)
        if now - c["first_seen"] > s["max_wait_min"] * 60:
            self.decide(c, pair, "kill", "never qualified", m)

    async def top10(self, mint, pool):
        h = await self.d.src.holders(mint, pool)
        sup = self.d.db.one("SELECT supply_raw FROM coins WHERE mint=?", mint)["supply_raw"]
        if not sup:
            j = await self.d.src.token_supply(mint)
            sup = F.get(j, F.RPC["supply"])
            self.d.db.x("UPDATE coins SET supply_raw=?, decimals=? WHERE mint=?", sup,
                        F.get(j, F.RPC["supply_decimals"]), mint)
        sup = int(sup or 0)
        return 100.0 * h["top10_raw"] / sup if sup else None

    def decide(self, c, pair, verdict, reason, m):
        db, mint = self.d.db, c["mint"]
        out = {"agent": "scanner", "mint": mint, "name": c.get("name"), "symbol": c.get("symbol"),
               "pool": c.get("pool"), "liquidityUsd": m.get("liq"), "top10Pct": m.get("top10"),
               "h1ChangePct": m.get("h1"), "ageMin": m.get("age"), "verdict": verdict, "reason": reason, "ts": iso()}
        db.x("INSERT INTO scans(ts,mint,verdict,json) VALUES(?,?,?,?)", time.time(), mint, verdict, json.dumps(out))
        db.x("UPDATE coins SET status=?, reason=?, liq_at_pass=? WHERE mint=?",
             "passed" if verdict == "pass" else "killed", reason, m.get("liq"), mint)
        db.decision("scanner", mint, c.get("name") or mint[:6], verdict, reason)
        self.L.info(f"{out['ts']} {mint} {verdict} {reason} liq={m.get('liq')} top10={m.get('top10')} h1={m.get('h1')} age={m.get('age')}")
        nm = c.get("name") or mint[:6]
        row = {"name": nm, "verdict": verdict, "reason": reason,
               "text": f"{nm} · liq ${money(m.get('liq') or 0)} · → auditor" if verdict == "pass" else nm}
        card = self.d.live.setdefault("scanner", {"feed": []})
        card["feed"] = ([row] + card.get("feed", []))[:6]
        self.d.event("scan", {"mint": mint, "verdict": verdict, "reason": reason})
        if verdict == "pass" and mint not in self.passed_tonight:
            self.passed_tonight.add(mint)
            self.d.bus["auditor"].put_nowait(out)

    async def run(self):
        await asyncio.gather(self.ws_loop(), self.poll_loop())
