"""Test 5: run the real Auditor against a live mint (no writes to the desk DB)."""
import asyncio, json, sys, tempfile, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desk.db import DB
from desk.sources import Sources
from desk.core import Desk
from desk.auditor import Auditor


class NullNotifier:
    chat_id = 0
    async def push(self, *a, **k):
        print("PUSH", a[0]); return 1


async def main(mint):
    d = tempfile.mkdtemp()
    db = DB(os.path.join(d, "t.db"))
    src = Sources(lambda: db.settings()["general"]["rpc_url"])
    await src.start()
    desk = Desk(db, src, NullNotifier(), logdir=d)
    db.x("INSERT INTO coins(mint,name,symbol,first_seen,migrated_at,status) VALUES(?,?,?,?,?,?)", mint, "test", "T", 0, 0, "passed")
    rep = await src.rug(mint, "report")
    pairs = await src.dex_tokens([mint]); pr = src.pumpswap_pair(pairs.get(mint, []))
    pool = pr["pairAddress"] if pr else None
    db.x("UPDATE coins SET creator=?, creator_bal_raw=?, pool=?, migrated_at=? WHERE mint=?", rep.get("creator"), rep.get("creatorBalance"), pool, (pr or {}).get("pairCreatedAt", 0) / 1000 or None, mint)
    out = await Auditor(desk).audit({"mint": mint, "name": "test", "pool": pool, "liquidityUsd": (pr or {}).get("liquidity", {}).get("usd")}, write=False)
    print(json.dumps(out, indent=1))
    await src.close()

asyncio.run(main(sys.argv[1]))
