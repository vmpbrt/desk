"""AGENT 5 · RISK. Exits don't bend. Never places an order; I tap Sell on FOMO."""
import asyncio, json, time
from . import fields as F, links, fees
from .core import iso
from .sources import RateLimited


def evaluate(pos, price, now, s):
    """pure rules. returns (updates dict, exit_reason or None). Stop never moves down:
    effective stop = tighter of the position's stop and the current setting."""
    stop_pct = min(pos["stop_pct"], s["stop_pct"])
    peak = max(pos["peak"] or price, price)
    upd = {"peak": peak, "stop_pct": stop_pct, "last_price": price}
    entry = pos["entry"]
    if price <= entry * (1 - stop_pct / 100):
        return upd, "stop"
    trail_active = bool(pos["trail_active"]) or price >= entry * (1 + pos["trail_on_pct"] / 100)
    upd["trail_active"] = int(trail_active)
    if trail_active and price <= peak * (1 - pos["trail_pct"] / 100):
        return upd, "trail"
    if now - pos["opened_at"] >= s["timeout_h"] * 3600 and price < entry * (1 + s["timeout_min_gain_pct"] / 100):
        return upd, "timeout"
    return upd, None


def exit_line(pos, s):
    stop = pos["entry"] * (1 - min(pos["stop_pct"], s["stop_pct"]) / 100)
    if pos["trail_active"]:
        return max(stop, pos["peak"] * (1 - pos["trail_pct"] / 100))
    return stop


class Risk:
    name = "risk"

    def __init__(self, desk):
        self.d = desk
        self.L = desk.log("risk")

    async def run(self):
        while True:
            s = self.d.s["risk"]
            try:
                await self.poll_once()
            except RateLimited:
                self.L.info(f"{iso()} 429, backing off {s['backoff_429_s']}s (polls missed)")
                await asyncio.sleep(s["backoff_429_s"])
            except Exception as e:  # noqa
                self.L.info(f"{iso()} poll error {type(e).__name__}: {e}")
            self.d.db.heartbeat("risk", f"open={len(self.d.db.open_positions())}")
            await asyncio.sleep(s["poll_s"])

    async def poll_once(self, pairs=None):
        db = self.d.db
        opens = db.q("SELECT * FROM positions WHERE status='open'")
        await self.resend_exiting()
        if not opens:
            return
        if pairs is None:
            pairs = await self.d.src.dex_tokens(list({p["mint"] for p in opens}))
        s = self.d.s["risk"]
        now = time.time()
        for p in opens:
            pair = self.d.src.pumpswap_pair(pairs.get(p["mint"], []))
            price = F.get(pair, F.DEXSCREENER["priceUsd"]) if pair else None
            if pair:
                self.d.last_pair[p["mint"]] = pair
            if price is None:
                n = p["no_price"] + 1
                db.x("UPDATE positions SET no_price=? WHERE id=?", n, p["id"])
                self.L.info(f"{iso()} {p['mint']} no price ({n})")
                if n == s["no_price_polls"]:
                    await self.d.notifier.push(f"{p['name']}: no price for 1 min",
                                               [("Sell on FOMO", links.coin_url(p["mint"], "solana"))], mint=p["mint"])
                continue
            price = float(price)
            db.price(p["mint"], price, F.get(pair, F.DEXSCREENER["volume_m5"]))
            liq = F.get(pair, F.DEXSCREENER["liquidityUsd"])
            upd, reason = evaluate(p, price, now, s)
            upd["no_price"] = 0
            if liq is not None and liq <= 0:
                reason = "pulled"
                upd["last_price"] = p["last_price"] or price
            sets = ", ".join(f"{k}=?" for k in upd)
            db.x(f"UPDATE positions SET {sets} WHERE id=?", *upd.values(), p["id"])
            p.update(upd)
            self.L.info(f"{iso()} {p['mint']} price={price} peak={p['peak']} stop_line={p['entry']*(1-p['stop_pct']/100):.10g} "
                        f"trail_line={(p['peak']*(1-p['trail_pct']/100)) if p['trail_active'] else None} action={reason or 'hold'}")
            if reason:
                await self.exit(p, upd["last_price"] if reason == "pulled" else price, reason)
        card = self.d.live.setdefault("risk", {})
        card["guarding"] = bool(db.open_positions())

    async def exit(self, p, price, reason):
        db, S = self.d.db, self.d.s
        mode = p["mode"]
        pnl, pct, fee = fees.paper_pnl(p["size"], p["entry"], price, S["general"]["fee_pct"])
        link = links.coin_url(p["mint"], "solana")
        stop_pct = p["stop_pct"]
        name = p["name"]
        if reason == "trail":
            text = f"Sold {name} · +${pnl:.2f} (+{pct:.1f}%)" if pnl >= 0 else f"Sold {name} · -${abs(pnl):.2f} ({pct:.1f}%)"
        elif reason == "stop":
            text = f"{name} hit the -{stop_pct:g}% stop · -${abs(pnl):.2f}"
        elif reason == "timeout":
            text = f"Sell {name}, dead money · {pct:+.1f}% in {S['risk']['timeout_h']:g}h"
        else:
            text = f"Sell {name}, liquidity pulled · {pct:+.1f}%"
        status = "closed" if mode == "PAPER" else "exiting"
        db.x("""UPDATE positions SET status=?, exit_reason=?, exit_price=?, pnl=?, pct=?, fees=?, closed_at=?,
                last_push_at=? WHERE id=?""", status, reason, price, pnl, pct, fee,
             time.time() if status == "closed" else None, time.time(), p["id"])
        cbs = [] if mode == "PAPER" else [("Sold", f"sold:{p['id']}")]
        await self.d.notifier.push(text + (" · PAPER" if mode == "PAPER" else ""), [("Sell on FOMO", link)], cbs, mint=p["mint"])
        out = {"agent": "risk", "mint": p["mint"], "name": name, "ts": iso(), "reason": reason,
               "entryPrice": p["entry"], "exitPrice": price, "peakPrice": p["peak"], "pnlUsd": pnl, "pct": pct,
               "feesUsd": fee, "stopPct": stop_pct, "mode": mode, "status": status, "link": link}
        self.d.event("exit", out)
        db.decision("risk", p["mint"], name, reason, f"{pct:+.1f}%")
        if status == "closed":
            self.log_activity(p, reason, pnl, pct, stop_pct)
        self.d.live.setdefault("risk", {})["last"] = "Trailed out" if reason == "trail" else "Stopped" if reason == "stop" else reason.capitalize()
        return out

    def log_activity(self, p, reason, pnl, pct, stop_pct):
        db = self.d.db
        if reason == "stop":
            db.activity("stop", f"Stopped {p['name']}", f"Risk · -{stop_pct:g}% stop", f"-${abs(pnl):.2f}", p["mint"], 0)
        else:
            sub = {"trail": "Risk · trailing stop", "timeout": "Risk · timeout", "pulled": "Risk · liquidity pulled"}[reason]
            sign = "+" if pnl >= 0 else "-"
            db.activity("sell", f"Sold {p['name']}", sub, f"{sign}${abs(pnl):.2f} · {pct:+.1f}%", p["mint"], int(pnl >= 0))

    async def resend_exiting(self):
        db, s = self.d.db, self.d.s["risk"]
        for p in db.q("SELECT * FROM positions WHERE status='exiting' AND resent=0"):
            if time.time() - (p["last_push_at"] or 0) > s["resend_min"] * 60:
                db.x("UPDATE positions SET resent=1 WHERE id=?", p["id"])
                await self.d.notifier.push(f"{p['name']} still open", [("Sell on FOMO", links.coin_url(p["mint"], "solana"))],
                                           [("Sold", f"sold:{p['id']}")], mint=p["mint"])

    def on_sold_tap(self, pos_id):
        db = self.d.db
        p = db.one("SELECT * FROM positions WHERE id=?", int(pos_id))
        if not p or p["status"] != "exiting":
            return "nothing to close"
        db.x("UPDATE positions SET status='closed', closed_at=? WHERE id=?", time.time(), p["id"])
        self.log_activity(p, p["exit_reason"], p["pnl"], p["pct"], p["stop_pct"])
        return f"Closed {p['name']}"
