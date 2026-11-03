"""AGENT 2 · AUDITOR. 8 layers in order, stop at first red. No data is never a pass."""
import asyncio, json, time
from . import fields as F
from .core import iso, night_id, short
from .sources import RateLimited

NAMES = ["Mint authority", "Freeze authority", "Dev wallet", "Top 10 holders", "Liquidity",
         "Pool age", "Bundled wallets", "Delegations"]
NODATA = ["mint", "freeze", "dev", "top 10", "liquidity", "pool age", "bundles", "delegations"]


class Red(Exception):
    def __init__(self, value, source):
        super().__init__(value)
        self.value, self.source = value, source


def bundled_size(graph):
    """graph = RugCheck insiders/graph (list of networks). Group = connected component over `links`;
    size = holders (holdings > 0) in the biggest group. [] -> 0. Missing links key on a network -> None."""
    if graph == []:
        return 0
    if not isinstance(graph, list):
        return None
    best = 0
    for net in graph:
        if not isinstance(net, dict) or "links" not in net or "nodes" not in net:
            return None
        hold = {n["id"] for n in net["nodes"] if (n.get("holdings") or 0) > 0}
        parent = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for n in net["nodes"]:
            find(n["id"])
        for l in net["links"]:
            ra, rb = find(l["source"]), find(l["target"])
            if ra != rb:
                parent[ra] = rb
        comp = {}
        for h in hold:
            comp[find(h)] = comp.get(find(h), 0) + 1
        if comp:
            best = max(best, max(comp.values()))
    return best


class Auditor:
    name = "auditor"

    def __init__(self, desk):
        self.d = desk
        self.L = desk.log("auditor")
        self.rejected = {}  # mint -> night
        self.l8_alerted = False

    async def run(self):
        while True:
            scan = await self.d.bus["auditor"].get()
            while self.d.db.paused("auditor"):
                await asyncio.sleep(2)
            try:
                await self.audit(scan)
            except Exception as e:  # noqa
                self.L.info(f"{iso()} {scan.get('mint')} crashed {type(e).__name__}: {e}")
            self.d.db.heartbeat("auditor", "")

    async def retry(self, fn, idx):
        s = self.d.s["auditor"]
        last = None
        for i in range(s["retries"]):
            try:
                return await fn()
            except Red:
                raise
            except Exception as e:  # noqa
                last = e
                await asyncio.sleep(s["retry_gap_s"] * (5 if isinstance(e, RateLimited) else 1))
        self.L.info(f"{iso()} layer {idx+1} no data after retries: {last}")
        if idx == 7 and not self.l8_alerted:
            self.l8_alerted = True
            await self.d.notifier.push("layer 8 down, every coin is red until rpc_url changes")
        if idx == 7:
            k = f"l8_kills:{night_id()}"
            self.d.db.kv_set(k, self.d.db.kv_get(k, 0) + 1)
        raise Red(f"no data: {NODATA[idx]}", "rpc")

    async def audit(self, scan, write=True):
        db, mint, s = self.d.db, scan["mint"], self.d.s["auditor"]
        if self.rejected.get(mint) == night_id():
            return None
        coin = db.one("SELECT * FROM coins WHERE mint=?", mint) or {}
        name = scan.get("name") or coin.get("name") or mint[:6]
        if not s["enabled"]:  # "Take apart before buying" off
            out = {"agent": "auditor", "mint": mint, "ts": iso(), "passed": True, "layersChecked": 0,
                   "layers": [], "holders": None, "reason": "auditor off"}
            self.d.bus["analyst"].put_nowait({"scan": scan, "audit": out})
            return out
        card = self.d.live.setdefault("auditor", {})
        card.update(running=True, mint=mint, name=name, layers=[None] * 8)
        cache = {}

        async def report():
            if "report" not in cache:
                cache["report"] = await self.d.src.rug(mint, "report")
            return cache["report"]

        async def graph():
            if "graph" not in cache:
                cache["graph"] = await self.d.src.rug(mint, "graph")
            return cache["graph"]

        async def mintinfo():
            if "mi" not in cache:
                cache["mi"] = await self.d.src.mint_info(mint)
            return cache["mi"]

        pool = scan.get("pool") or coin.get("pool")
        holders_n = None

        async def L1():
            mi = await mintinfo()
            if F.get(mi, F.RPC["mintAuthority"], "MISSING") is not None:
                raise Red("mint not revoked", "rpc")
            return "Revoked", "rpc"

        async def L2():
            mi = await mintinfo()
            if F.get(mi, F.RPC["freezeAuthority"], "MISSING") is not None:
                raise Red("freeze not revoked", "rpc")
            return "Revoked", "rpc"

        async def L3():
            mi = await mintinfo()
            sup = F.get(mi, F.RPC["supply_acct"])
            try:
                sup = int(sup)
            except (TypeError, ValueError):
                sup = 0
            if not sup:
                raise Red("no supply", "rpc")
            cache["supply"] = sup
            creator = coin.get("creator")
            if not creator:
                raise Red("no data: dev", "rugcheck")
            bal = await self.d.src.token_balance(creator, mint)
            pct = 100.0 * bal / sup
            at_mig = coin.get("creator_bal_raw")
            if pct >= s["max_dev_pct"]:
                raise Red(f"dev holds {pct:.0f}%", "rpc")
            if at_mig is not None and bal < int(at_mig):
                raise Red("dev sold", "rpc")
            return f"{pct:.1f}% · sold 0", "rpc"

        async def L4():
            h = await self.d.src.holders(mint, pool)
            sup = cache.get("supply") or int(F.get(await mintinfo(), F.RPC["supply_acct"]) or 0)
            pct = 100.0 * h["top10_raw"] / sup
            if pct >= s["max_top10_pct"]:
                raise Red(f"top 10 hold {pct:.0f}%", "rpc")
            return f"{pct:.1f}%", "rpc"

        async def L5():
            rep = await report()
            mk = [m for m in (F.get(rep, F.RUGCHECK_REPORT["markets"]) or []) if m.get("pubkey") == pool]
            if not mk:
                raise Red("no data: liquidity", "rugcheck")
            lp = F.get(mk[0], "lp.lpLockedPct")
            if lp is None:
                raise Red("no data: liquidity", "rugcheck")
            if lp < s["min_lp_pct"]:
                raise Red("liquidity unlocked", "rugcheck")
            liq0 = scan.get("liquidityUsd") or coin.get("liq_at_pass")
            pairs = await self.d.src.dex_tokens([mint])
            pr = self.d.src.pumpswap_pair(pairs.get(mint, []))
            liq_now = F.get(pr, F.DEXSCREENER["liquidityUsd"]) if pr else None
            if liq_now is None:
                raise Red("no data: liquidity", "rugcheck")
            if liq0 and liq_now < liq0 * (1 - s["max_liq_drop_pct"] / 100):
                raise Red("liquidity pulled", "rugcheck")
            return f"{lp:.0f}% locked", "rugcheck"

        async def L6():
            mig = coin.get("migrated_at") or time.time()
            age = (time.time() - mig) / 60
            if age < s["min_age_min"]:
                raise Red("pool too young", "rpc")
            return (f"{int(age)}m" if age < 120 else f"{age/60:.0f}h"), "rpc"

        async def L7():
            g = await graph()
            n = bundled_size(g)
            if n is None:
                raise Red("no data: bundles", "rugcheck")
            cache["bundled"] = n
            if n >= s["max_bundle"]:
                raise Red(f"{n} bundled wallets", "rugcheck")
            return ("None" if n == 0 else f"{n} linked"), "rugcheck"

        async def L8():
            nonlocal holders_n
            h = await self.d.src.holders(mint, pool, max_age=0)
            holders_n = h["holders"]
            if h["delegates"]:
                d, cnt = max(h["delegates"].items(), key=lambda kv: kv[1])
                if cnt >= s["max_delegate"]:
                    raise Red(f"{cnt} wallets → {short(d)}", "rpc")
            return "None", "rpc"

        layers, passed, reason = [], True, None
        for i, fn in enumerate([L1, L2, L3, L4, L5, L6, L7, L8]):
            t0 = time.time()
            try:
                val, src = await self.retry(fn, i)
                ok = True
            except Red as r:
                val, src, ok = r.value, r.source, False
            ms = int((time.time() - t0) * 1000)
            layer = {"n": i + 1, "name": NAMES[i], "pass": ok, "value": val, "source": src}
            layers.append(layer)
            card["layers"][i] = ok
            self.d.event("audit_layer", {"mint": mint, **layer})
            self.L.info(f"{iso()} {mint} L{i+1} {NAMES[i]} {'ok' if ok else 'RED'} {val} {ms}ms")
            if not ok:
                passed, reason = False, val
                break
        out = {"agent": "auditor", "mint": mint, "ts": iso(), "passed": passed, "layersChecked": len(layers),
               "layers": layers, "holders": holders_n, "reason": reason, "bundled": cache.get("bundled")}
        card.update(running=False, last={"name": name, "passed": passed, "reason": reason})
        if not write:
            return out
        db.x("INSERT INTO audits(ts,mint,passed,json) VALUES(?,?,?,?)", time.time(), mint, int(passed), json.dumps(out))
        if holders_n is not None:
            db.x("UPDATE coins SET holders=? WHERE mint=?", holders_n, mint)
        db.decision("auditor", mint, name, "pass" if passed else "reject", reason)
        self.L.info(f"{iso()} {mint} verdict {'PASS 8/8' if passed else 'REJECT ' + str(reason)}")
        if passed:
            self.d.bus["analyst"].put_nowait({"scan": scan, "audit": out})
        else:
            self.rejected[mint] = night_id()
            db.activity("reject", f"Rejected {name}", f"Auditor · {reason}", "Skipped", mint, 0)
        return out
