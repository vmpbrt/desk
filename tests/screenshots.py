"""Test 8: headless 390px screenshots. Builds a demo site in a temp dir (real FK5t audit + a simulated PAPER
position) and also shoots the live local site. The key goes in via localStorage, never printed."""
import asyncio, os, sys, tempfile, time, json, subprocess, socket
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desk.db import DB
from desk.sources import Sources
from desk.core import Desk
from desk.auditor import Auditor
from desk.executor import Executor
from desk.publisher import Publisher
from desk import crypto
from playwright.async_api import async_playwright

MINT = "FK5tP4Zw7ujWzaz3TgeJ5NcqGe5LFufo7m6zH6YQpump"
OUT = "/workspace/desk/screenshots"
class NullN:
    chat_id = 0
    async def push(self, *a, **k): return 1

async def build_demo(site, key):
    d = tempfile.mkdtemp(); db = DB(os.path.join(d, "t.db"))
    src = Sources(lambda: db.settings()["general"]["rpc_url"]); await src.start()
    desk = Desk(db, src, NullN(), logdir=d)
    rep = await src.rug(MINT, "report"); pairs = await src.dex_tokens([MINT]); pr = src.pumpswap_pair(pairs.get(MINT, []))
    name = (pr or {}).get("baseToken", {}).get("name", "demo"); sym = (pr or {}).get("baseToken", {}).get("symbol", "D")
    db.x("INSERT INTO coins(mint,name,symbol,first_seen,migrated_at,status,creator,creator_bal_raw,pool) VALUES(?,?,?,?,?,?,?,?,?)",
         MINT, name, sym, time.time(), (pr or {}).get("pairCreatedAt", 0)/1000, "new", rep.get("creator"), rep.get("creatorBalance"), (pr or {}).get("pairAddress"))
    await Auditor(desk).audit({"mint": MINT, "name": name, "pool": (pr or {}).get("pairAddress"),
                               "liquidityUsd": (pr or {}).get("liquidity", {}).get("usd")}, write=True)
    price = float((pr or {}).get("priceUsd") or 0.001)
    now = time.time()
    for i in range(120):  # simulated price path for the chart (demo only)
        db.x("INSERT INTO prices(mint,ts,price,vol_m5) VALUES(?,?,?,?)", MINT, now-7200+i*60, price*(0.9+0.002*i), 0)
    Executor(desk).open_position(MINT, name, sym, price*0.95, 18.0, "PAPER")
    await src.close()
    p = Publisher(desk, key, site=site); os.makedirs(site, exist_ok=True); p.write()

def serve(path, port):
    return subprocess.Popen([sys.executable, "-m", "http.server", str(port), "-d", path, "-b", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def shoot(port, tag, routes, key):
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        await ctx.add_init_script(f"if(!location.search.includes('force=locked'))localStorage.setItem('desk.k', {json.dumps(crypto.b64url(key))});else localStorage.clear();")
        pg = await ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        out = []
        for name, q, h, wait in routes:
            await pg.goto(f"http://127.0.0.1:{port}/{q}#{h}"); await pg.wait_for_timeout(wait)
            f = f"{OUT}/{tag}-{name}.png"; await pg.screenshot(path=f, full_page=True); out.append(f)
        await b.close()
        return out, errs

async def main():
    key = crypto.load_or_create_key()
    demo = tempfile.mkdtemp() + "/site"; await build_demo(demo, key)
    routes = [("home", "", "/home", 2500), ("agents", "", "/agents", 1500), ("agent-auditor", "", "/agent/auditor", 1500),
              ("activity", "", "/activity", 1500), ("account", "", "/account", 1500),
              ("coin", "", f"/coin/{MINT}", 2000), ("audit", "", f"/audit/{MINT}", 4000)]
    states = [(f"{s}-{n}", f"?force={s}", h, 1500) for s in ("loading", "empty", "error") for n, _, h, _ in routes] + [("locked", "?force=locked", "/home", 1500)]
    p1 = serve(demo, 8771); p2 = serve("/workspace/desk/publish/site", 8772); await asyncio.sleep(1)
    try:
        a, e1 = await shoot(8771, "demo", routes + states, key)
        b, e2 = await shoot(8772, "live", routes[:5], key)
    finally:
        p1.kill(); p2.kill()
    print(len(a) + len(b), "screenshots"); print("page errors:", e1 + e2 or "none")
asyncio.run(main())
