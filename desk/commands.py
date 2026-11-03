"""Telegram control (plan B: the app is read-only). Owner chat only."""
import asyncio, json, time
from . import config, links
from .core import iso, night_start_ts, money

AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"]
HELP = """Desk commands
status · how the desk is doing
why <coin> · why a coin was passed or rejected
pause <agent> / resume <agent> (scanner auditor analyst executor risk)
stack <usd> · set the LIVE stack, e.g. stack 1240
mode paper / mode live (type LIVE to confirm)
set <group.key> <value> · e.g. set risk.stop_pct 5
settings [group] · show settings
changes · last setting changes
rpc <url> · change the Solana RPC
test · send a test push
report · morning report now"""


class Commands:
    def __init__(self, desk, executor, risk, report_fn):
        self.d, self.ex, self.risk, self.report_fn = desk, executor, risk, report_fn
        self.L = desk.log("commands")
        self.await_live = 0

    async def run(self):
        while True:
            try:
                for kind, val in await self.d.notifier.updates():
                    if kind == "text":
                        out = await self.handle(val)
                        if out:
                            await self.d.notifier.reply(out)
                    else:
                        out = await self.callback(val["data"])
                        await self.d.notifier.answer_callback(val["id"], out[:190])
                        await self.d.notifier.reply(out)
            except Exception as e:  # noqa
                self.L.info(f"{iso()} error {type(e).__name__}: {e}")
                await asyncio.sleep(5)
            self.d.db.heartbeat("commands", "")

    async def callback(self, data):
        k, _, v = data.partition(":")
        if k == "bought":
            return await self.ex.on_bought_tap(v)
        if k == "sold":
            return self.risk.on_sold_tap(v)
        return "?"

    def find(self, q):
        db = self.d.db
        q = q.strip().lstrip("$")
        return db.one("""SELECT * FROM coins WHERE mint=? OR lower(symbol)=lower(?) OR lower(name)=lower(?)
                         ORDER BY first_seen DESC LIMIT 1""", q, q, q)

    async def handle(self, text):
        db = self.d.db
        t = text.strip()
        low = t.lower().lstrip("/")
        self.L.info(f"{iso()} cmd {t[:80]}")
        if t == "LIVE" and time.time() - self.await_live < 120:
            self.await_live = 0
            if db.open_positions():
                return "Close the open positions first; mode can't change while one is open."
            db.set_setting("general.mode", "LIVE")
            return f"LIVE on. Stack ${self.d.s['general']['stack']:.2f}. Every buy waits for your tap."
        parts = low.split()
        if not parts:
            return None
        cmd, args = parts[0], t.split()[1:]
        if cmd in ("help", "start"):
            return HELP
        if cmd == "status":
            return self.status()
        if cmd == "why" and args:
            return self.why(" ".join(args))
        if cmd in ("pause", "resume") and args:
            a = args[0].lower()
            if a not in AGENTS:
                return f"agents: {', '.join(AGENTS)}"
            if cmd == "pause" and a == "risk" and db.open_positions():
                return "Risk can't be paused while a position is open."
            db.x("UPDATE agent_state SET paused=? WHERE agent=?", int(cmd == "pause"), a)
            db.x("INSERT INTO change_log(ts,key,old,new,source) VALUES(?,?,?,?,?)", time.time(), f"paused.{a}",
                 json.dumps(cmd != "pause"), json.dumps(cmd == "pause"), "telegram")
            return f"{a} {'paused' if cmd == 'pause' else 'running'}"
        if cmd == "stack" and args:
            ok, v = config.validate("general.stack", args[0], self.d.s)
            if not ok:
                return v
            old = db.set_setting("general.stack", v)
            return f"Stack ${old} → ${v}"
        if cmd == "mode" and args:
            m = args[0].lower()
            if m == "paper":
                if db.open_positions():
                    return "Close the open positions first; mode can't change while one is open."
                db.set_setting("general.mode", "PAPER")
                return "PAPER on."
            if m == "live":
                self.await_live = time.time()
                return "Type LIVE (capitals) within 2 min to switch to real money."
        if cmd == "set" and len(args) >= 2:
            key = args[0]
            if key in config.LOCKED or key == "general.mode":
                return f"{key} has its own command"
            ok, v = config.validate(key, " ".join(args[1:]), self.d.s)
            if not ok:
                return v
            old = db.set_setting(key, v)
            return f"{key}: {old} → {v}"
        if cmd == "settings":
            s = self.d.s
            gs = [args[0]] if args and args[0] in s else list(s)
            return "\n".join(f"{g}.{k} = {v}" for g in gs for k, v in s[g].items())[:3900]
        if cmd == "changes":
            rows = db.q("SELECT * FROM change_log ORDER BY id DESC LIMIT 10")
            return "\n".join(f"{time.strftime('%m-%d %H:%M', time.localtime(r['ts']))} {r['key']} {r['old']} → {r['new']}" for r in rows) or "no changes"
        if cmd == "rpc" and args:
            if not args[0].startswith("https://"):
                return "rpc must start with https://"
            old = db.set_setting("general.rpc_url", args[0])
            self.d.src._holder_cache.clear()
            return "RPC changed."
        if cmd == "test":
            mid = await self.d.notifier.push("Test push from your desk · PAPER", [("Open FOMO", links.SIGNUP_URL)])
            return None if mid else "test push failed"
        if cmd == "report":
            return await self.report_fn(send=False)
        return "Unknown. Send help."

    def status(self):
        db, s = self.d.db, self.d.s
        hb = {r["agent"]: r["ts"] for r in db.q("SELECT * FROM heartbeats")}
        ns = night_start_ts()
        lines = [f"{s['general']['mode']} · balance ${db.balance_now():.2f}"]
        for a in AGENTS:
            age = int(time.time() - hb[a]) if a in hb else None
            n = db.one("SELECT COUNT(*) n FROM decisions WHERE agent=? AND ts>=?", a, ns)["n"]
            lines.append(f"{a}: {'paused' if db.paused(a) else 'on'} · {n} tonight · hb {age}s ago" if age is not None else f"{a}: no heartbeat")
        for p in db.open_positions():
            lines.append(f"open {p['name']} ${p['size']:.2f} @ {p['entry']:.8g} → {p['last_price'] or 0:.8g}")
        return "\n".join(lines)

    def why(self, q):
        c = self.find(q)
        if not c:
            return "I haven't seen that coin."
        rows = self.d.db.q("SELECT * FROM decisions WHERE mint=? ORDER BY id", c["mint"])
        lines = [f"{c['name']} ({c['symbol']})"] + [f"{r['agent']}: {r['verdict']} · {r['reason']}" for r in rows]
        lines.append(links.coin_url(c["mint"], "solana"))
        return "\n".join(lines)
