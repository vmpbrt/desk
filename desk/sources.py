"""Outside data: DexScreener, RugCheck, Solana RPC. Raw shapes read only through fields.py."""
import asyncio, json, logging, time
import aiohttp
from . import fields as F
from .solutil import bonding_curve

log = logging.getLogger("sources")
DEX = "https://api.dexscreener.com/tokens/v1/solana/"
RUG = "https://api.rugcheck.xyz/v1/tokens/"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


class RateLimited(Exception):
    pass


class SourceError(Exception):
    pass


class Sources:
    def __init__(self, rpc_url_fn, dex_rpm_fn=lambda: 250):
        self.rpc_url_fn = rpc_url_fn
        self.dex_rpm_fn = dex_rpm_fn
        self.session = None
        self._dex_times = []
        self._holder_cache = {}  # mint -> (ts, result)
        self._holder_locks = {}
        self.dex_calls = 0

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                             headers={"User-Agent": "desk/1.0"})

    async def close(self):
        if self.session:
            await self.session.close()

    # ---------- DexScreener ----------
    async def _dex_slot(self):
        while True:
            now = time.time()
            self._dex_times = [t for t in self._dex_times if now - t < 60]
            if len(self._dex_times) < self.dex_rpm_fn():
                self._dex_times.append(now)
                return
            await asyncio.sleep(0.5)

    async def dex_tokens(self, mints):
        """batched (30 per call). returns {mint: [pairs]}; raises RateLimited on 429."""
        out = {m: [] for m in mints}
        for i in range(0, len(mints), 30):
            chunk = mints[i:i + 30]
            await self._dex_slot()
            self.dex_calls += 1
            async with self.session.get(DEX + ",".join(chunk)) as r:
                if r.status == 429:
                    raise RateLimited("dexscreener 429")
                if r.status != 200:
                    raise SourceError(f"dexscreener HTTP {r.status}")
                data = await r.json(content_type=None)
            for p in data or []:
                m = F.get(p, F.DEXSCREENER["baseAddress"])
                if m in out:
                    out[m].append(p)
        return out

    @staticmethod
    def pumpswap_pair(pairs):
        """the dexId pumpswap pair with the most liquidity, or None"""
        ps = [p for p in pairs if F.get(p, F.DEXSCREENER["dexId"]) == "pumpswap"
              and F.get(p, F.DEXSCREENER["liquidityUsd"]) is not None]
        if not ps:
            return None
        return max(ps, key=lambda p: F.get(p, F.DEXSCREENER["liquidityUsd"], 0) or 0)

    # ---------- RugCheck ----------
    async def rug(self, mint, which):
        url = RUG + mint + ("/report" if which == "report" else "/insiders/graph")
        async with self.session.get(url) as r:
            if r.status == 429:
                raise RateLimited("rugcheck 429")
            if r.status != 200:
                raise SourceError(f"rugcheck {which} HTTP {r.status}")
            return await r.json(content_type=None)

    # ---------- RPC ----------
    async def rpc(self, method, params, timeout=30):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with self.session.post(self.rpc_url_fn(), json=body,
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            j = await r.json(content_type=None)
        if r.status == 429 or (isinstance(j, dict) and F.get(j, "error.code") == 429):
            raise RateLimited(f"rpc {method} 429")
        if "error" in j:
            raise SourceError(f"rpc {method} error {json.dumps(j['error'])[:200]}")
        return j

    async def mint_info(self, mint):
        j = await self.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        if F.get(j, "result.value") is None:
            raise SourceError("mint account not found")
        return j

    async def token_supply(self, mint):
        return await self.rpc("getTokenSupply", [mint])

    async def token_balance(self, owner, mint):
        """sum of owner's token accounts for mint (raw int)"""
        j = await self.rpc("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        tot = 0
        for a in F.get(j, "result.value", []) or []:
            tot += int(F.get(a, F.RPC["gpa_amount"], "0") or 0)
        return tot

    async def holders(self, mint, pool, max_age=45):
        """Layer-8 fetch, shared by Scanner (top 10) and Auditor (top 10 + delegations), cached.
        getProgramAccounts on Token (dataSize 165 + memcmp) and Token-2022 (memcmp)."""
        c = self._holder_cache.get(mint)
        if c and time.time() - c[0] < max_age and c[1]["pool"] == pool:
            return c[1]
        lock = self._holder_locks.setdefault(mint, asyncio.Lock())
        async with lock:
            c = self._holder_cache.get(mint)
            if c and time.time() - c[0] < max_age and c[1]["pool"] == pool:
                return c[1]
            accts = []
            for prog, flt in ((TOKEN, [{"dataSize": 165}, {"memcmp": {"offset": 0, "bytes": mint}}]),
                              (TOKEN22, [{"memcmp": {"offset": 0, "bytes": mint}}])):
                j = await self.rpc("getProgramAccounts", [prog, {"encoding": "jsonParsed", "filters": flt}], timeout=60)
                accts += j.get("result") or []
            curve = bonding_curve(mint)
            excluded = {pool, curve}
            by_owner, delegates, holders = {}, {}, 0
            for a in accts:
                owner = F.get(a, F.RPC["gpa_owner"])
                amt = int(F.get(a, F.RPC["gpa_amount"], "0") or 0)
                if amt > 0:
                    holders += 1
                d = F.get(a, F.RPC["gpa_delegate"])
                if d and amt > 0:
                    delegates[d] = delegates.get(d, 0) + 1
                if owner in excluded:
                    continue
                by_owner[owner] = by_owner.get(owner, 0) + amt
            top = sorted(by_owner.values(), reverse=True)[:10]
            res = {"pool": pool, "curve": curve, "accounts": len(accts), "holders": holders,
                   "top10_raw": sum(top), "delegates": delegates, "by_owner": by_owner}
            self._holder_cache[mint] = (time.time(), res)
            return res
