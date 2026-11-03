"""AGENT 4 · EXECUTOR. Arithmetic + 1 button. Never places an order."""
import asyncio, json, time
from . import fields as F, links
from .core import iso, night_id, money


def size_order(stack, free, liq_usd, s):
    """returns (size or None, cap_hit, reason). ASSUMPTION for the part of SIZE not available:
    size > free -> skip 'no free stack'."""
    raw = stack * s["stack_pct"] / 100.0
    size, cap = raw, "stack_pct"
    liq_cap = (liq_usd or 0) * s["liq_pct"] / 100.0
    if liq_cap < size:
        size, cap = liq_cap, "liq_pct"
    if size < s["min_ticket"]:
        if s["below_min"] == "bump" and free >= s["min_ticket"]:
            return round(s["min_ticket"], 2), cap + "+bump", None
        return None, cap, "below min ticket" if s["below_min"] == "skip" else "below min, no free stack to bump"
    if size > free:
        return None, cap, "no free stack"
    return round(size, 2), cap, None


class Executor:
    name = "executor"

    def __init__(self, desk):
        self.d = desk
        self.L = desk.log("executor")
        self.pushed = {}  # mint -> night
        self.pending = {}  # signal id -> info (LIVE, waiting for my tap)

    async def run(self):
        asyncio.create_task(self.expire_loop())
        while True:
            item = await self.d.bus["executor"].get()
            while self.d.db.paused("executor"):
                await asyncio.sleep(2)
            try:
                await self.execute(item)
            except Exception as e:  # noqa
                self.L.info(f"{iso()} crashed {type(e).__name__}: {e}")
            self.d.db.heartbeat("executor", "")

    def free(self, stack):
        used = sum(p["size"] for p in self.d.db.open_positions())
        return stack - used

    async def execute(self, item, fill_delay=1.2):
        db, S = self.d.db, self.d.s
        s, mode = S["executor"], S["general"]["mode"]
        scan = item["scan"]
        mint, name = scan["mint"], scan.get("name") or scan["mint"][:6]
        if self.pushed.get(mint) == night_id() or db.one(
                "SELECT 1 FROM positions WHERE mint=? AND status IN ('open','exiting')", mint):
            return None  # 1 push per coin per night, never a 2nd buy
        stack = db.balance_now() if mode == "PAPER" else S["general"]["stack"]
        free = self.free(stack)
        pairs = await self.d.src.dex_tokens([mint])
        pair = self.d.src.pumpswap_pair(pairs.get(mint, []))
        price = float(F.get(pair, F.DEXSCREENER["priceUsd"])) if pair and F.get(pair, F.DEXSCREENER["priceUsd"]) else None
        liq = F.get(pair, F.DEXSCREENER["liquidityUsd"]) if pair else 0
        link = links.coin_url(mint, "solana")
        size, cap, reason = size_order(stack, free, liq, s)
        if price is None:
            size, reason = None, "no price"
        out = {"agent": "executor", "mint": mint, "signalPrice": price, "sizeUsd": size,
               "stackPct": round(100 * size / stack, 1) if size else None, "link": link, "mode": mode,
               "status": "skipped" if size is None else "sent", "reason": reason}
        self.L.info(f"{iso()} {mint} stack={stack:.2f} raw={stack*s['stack_pct']/100:.2f} cap={cap} final={size} mode={mode} status={out['status']} reason={reason} link={link}")
        self.pushed[mint] = night_id()
        if size is None:
            db.x("INSERT INTO skips(ts,mint,agent,reason) VALUES(?,?,?,?)", time.time(), mint, "executor", reason)
            db.decision("executor", mint, name, "skipped", reason)
            self._save(out)
            return out
        card = self.d.live.setdefault("executor", {})
        card.update(name=name, size=size, pct=out["stackPct"], progress=0, filled=False)
        pct = out["stackPct"]
        text = f"Buy {name} · ${size:.2f} · {pct}% of stack" + (" · PAPER" if mode == "PAPER" else "")
        cbs = [] if mode == "PAPER" else [("Bought", f"bought:{mint}")]
        mid = await self.d.notifier.push(text, [("Buy on FOMO", link)], cbs, mint=mint)
        if mid is None:
            self.L.info(f"{iso()} {mint} push failed")
            if mode == "LIVE":
                out.update(status="skipped", reason="push failed")
                self._save(out)
                return out
        if mode == "PAPER":
            for i in range(1, 11):
                card["progress"] = i * 10
                await asyncio.sleep(fill_delay / 10)
            self.open_position(mint, name, scan.get("symbol"), price, size, "PAPER")
            out["status"] = "bought"
        else:
            self.pending[mint] = {"ts": time.time(), "name": name, "symbol": scan.get("symbol"), "size": size}
            card["progress"] = 50
        self._save(out)
        return out

    def _save(self, out):
        self.d.db.x("INSERT INTO signals(ts,mint,status,json) VALUES(?,?,?,?)", time.time(), out["mint"], out["status"], json.dumps(out))
        self.d.event("signal", out)

    def open_position(self, mint, name, symbol, price, size, mode):
        db, r = self.d.db, self.d.s["risk"]
        stack = db.balance_now() if mode == "PAPER" else self.d.s["general"]["stack"]
        db.x("""INSERT INTO positions(mint,name,symbol,entry,size,peak,stop_pct,trail_pct,trail_on_pct,opened_at,status,mode,last_price)
                VALUES(?,?,?,?,?,?,?,?,?,?,'open',?,?)""", mint, name, symbol, price, size, price, r["stop_pct"],
             r["trail_pct"], r["trail_on_pct"], time.time(), mode, price)
        pct = round(100 * size / stack, 1)
        t = time.strftime("%H:%M")
        db.activity("buy", f"Bought {name}", f"Executor · {t}", f"${size:.2f} · {pct}% of stack", mint, None)
        db.decision("executor", mint, name, "bought", f"${size:.2f} @ {price}")
        card = self.d.live.setdefault("executor", {})
        card.update(progress=100, filled=True)
        self.d.event("fill", {"mint": mint, "size": size, "price": price})

    async def on_bought_tap(self, mint):
        p = self.pending.pop(mint, None)
        if not p:
            return "nothing pending for that coin"
        pairs = await self.d.src.dex_tokens([mint])
        pair = self.d.src.pumpswap_pair(pairs.get(mint, []))
        price = F.get(pair, F.DEXSCREENER["priceUsd"]) if pair else None
        if not price:
            return "no price right now, tap again"
        self.open_position(mint, p["name"], p["symbol"], float(price), p["size"], "LIVE")
        return f"Bought {p['name']} logged, Risk is guarding it"

    async def expire_loop(self):
        while True:
            lim = self.d.s["executor"]["live_tap_min"] * 60
            for mint, p in list(self.pending.items()):
                if time.time() - p["ts"] > lim:
                    self.pending.pop(mint)
                    self.d.db.x("INSERT INTO skips(ts,mint,agent,reason) VALUES(?,?,?,?)", time.time(), mint, "executor", "missed")
                    self._save({"agent": "executor", "mint": mint, "signalPrice": None, "sizeUsd": p["size"],
                                "stackPct": None, "link": links.coin_url(mint, "solana"), "mode": "LIVE",
                                "status": "missed", "reason": "no tap"})
            await asyncio.sleep(10)
