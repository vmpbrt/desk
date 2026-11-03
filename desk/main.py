"""One asyncio process: 5 agents + publisher + Telegram commands + balance sampler + morning report."""
import asyncio, datetime, json, os, signal, sys, time
from .core import ROOT, ET, Desk, iso, night_start_ts, night_id
from .db import DB
from .sources import Sources
from .notifier import Telegram
from .scanner import Scanner
from .auditor import Auditor
from .analyst import Analyst
from .executor import Executor
from .risk import Risk
from .publisher import Publisher
from .commands import Commands
from . import crypto, links

DB_PATH = os.environ.get("DESK_DB", os.path.join(ROOT, "desk.db"))


def owner_chat():
    with open(os.path.join(ROOT, "owner.json")) as f:
        return int(json.load(f)["owner_chat_id"])


async def sampler(desk):
    while True:
        desk.db.x("INSERT INTO balance VALUES(?,?)", time.time(), desk.db.balance_now())
        hb = {r["agent"]: r["ts"] for r in desk.db.q("SELECT * FROM heartbeats")}
        with open(os.path.join(ROOT, "logs", "heartbeat.json"), "w") as f:
            json.dump({"ts": time.time(), "iso": iso(), "pid": os.getpid(), "agents": hb}, f)
        await asyncio.sleep(15)


async def morning_report(desk, send=True):
    db = desk.db
    ns = night_start_ts() - (86400 if datetime.datetime.now(ET).hour < 12 else 0)
    k8 = db.kv_get(f"l8_kills:{night_id(ns + 60)}", 0)
    closed = db.q("SELECT * FROM positions WHERE closed_at>=?", ns)
    pnl = sum(p["pnl"] or 0 for p in closed)
    counts = {a: db.one("SELECT COUNT(*) n FROM decisions WHERE agent=? AND ts>=?", a, ns)["n"]
              for a in ("scanner", "auditor", "analyst", "executor", "risk")}
    lines = []
    if k8:
        lines.append(f"⚠ layer 8 had no data and killed {k8} coins")
    lines.append(f"Night {night_id(ns + 60)} · {db.settings()['general']['mode']} · balance ${db.balance_now():.2f} · "
                 f"P&L {'+' if pnl >= 0 else '-'}${abs(pnl):.2f}")
    lines.append(" · ".join(f"{a} {n}" for a, n in counts.items()))
    for p in closed:
        lines.append(f"{p['name']}: {p['exit_reason']} {p['pct']:+.1f}% (${p['pnl']:+.2f}) {links.coin_url(p['mint'], 'solana')}")
    for p in db.open_positions():
        lines.append(f"still open: {p['name']} ${p['size']:.2f}")
    text = "\n".join(lines)
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with open(os.path.join(ROOT, "reports", f"morning-{night_id(ns + 60)}.md"), "w") as f:
        f.write(text + "\n")
    if send:
        await desk.notifier.push("Morning report\n" + text)
    return text


async def morning_loop(desk):
    while True:
        now = datetime.datetime.now(ET)
        key = f"morning:{now:%Y-%m-%d}"
        if now.hour == 8 and not desk.db.kv_get(key):
            desk.db.kv_set(key, True)
            try:
                await morning_report(desk)
            except Exception as e:  # noqa
                desk.log("main").info(f"{iso()} morning report error {e}")
        await asyncio.sleep(30)


async def guard(name, coro_fn, desk):
    L = desk.log("main")
    while True:
        try:
            await coro_fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa
            L.info(f"{iso()} task {name} died {type(e).__name__}: {e}; restart in 5s")
            await asyncio.sleep(5)


async def amain():
    db = DB(DB_PATH)
    src = Sources(lambda: db.settings()["general"]["rpc_url"], lambda: db.settings()["scanner"]["dex_rpm"])
    await src.start()
    tg = Telegram(os.environ["TG_TOKEN"], owner_chat(), db)
    await tg.start()
    desk = Desk(db, src, tg)
    L = desk.log("main")
    L.info(f"{iso()} start pid={os.getpid()} mode={db.settings()['general']['mode']}")
    key = crypto.load_or_create_key()
    sc, au, an, ex, rk = Scanner(desk), Auditor(desk), Analyst(desk), Executor(desk), Risk(desk)
    pub = Publisher(desk, key)
    cmd = Commands(desk, ex, rk, lambda send=True: morning_report(desk, send))
    tasks = [("scanner", sc.run), ("auditor", au.run), ("analyst", an.run), ("executor", ex.run), ("risk", rk.run),
             ("publisher", pub.run), ("commands", cmd.run), ("sampler", lambda: sampler(desk)),
             ("morning", lambda: morning_loop(desk))]
    await asyncio.gather(*(guard(n, f, desk) for n, f in tasks))


def main():
    with open(os.path.join(ROOT, "logs", "desk.pid"), "w") as f:
        f.write(str(os.getpid()))
    asyncio.run(amain())


if __name__ == "__main__":
    main()
