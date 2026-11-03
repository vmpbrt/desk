"""AGENT 3 · ANALYST. Builds a numbers-only state, gets 4 answers (Jev or rules), code applies the gate."""
import asyncio, hashlib, json, os, time
import aiohttp
from . import fields as F
from .core import iso, money

JEV_URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "alive_6h": {"type": "noul", "instructions": "This coin is still actively traded 6 hours from now"},
    "organic": {"type": "noul", "instructions": "Most buyers in the last 30 minutes are separate real wallets"},
    "flush": {"type": "noul", "instructions": "Price just flushed down and buyers are absorbing it"},
    "chase": {"type": "noul", "instructions": "This entry is chasing a green candle that already ran"},
}


def clamp(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def rules_answers(pair, bundled, r):
    g = lambda k: F.get(pair, F.DEXSCREENER[k]) or 0  # noqa
    alive = clamp(g("volume_h1") / r["vol_h1_full"])
    organic = clamp(g("buys_h1") / r["buys_h1_full"])
    if (bundled or 0) >= r["bundle_min"]:
        organic *= r["bundle_factor"]
    m5 = g("priceChange_m5")
    if m5 <= -r["flush_drop_pct"] and g("buys_m5") >= g("sells_m5"):
        flush = 1.0
    elif m5 < 0:
        flush = 0.5
    else:
        flush = 0.0
    chase = clamp(g("priceChange_h1") / r["chase_full_pct"])
    return {"alive_6h": round(alive, 4), "organic": round(organic, 4), "flush": flush, "chase": round(chase, 4)}


def gate(ans, s):
    failed = []
    if ans["alive_6h"] < s["min_alive"]:
        failed.append(f"alive_6h {ans['alive_6h']:.2f}")
    if ans["organic"] < s["min_organic"]:
        failed.append(f"organic {ans['organic']:.2f}")
    if ans["chase"] > s["max_chase"]:
        failed.append(f"chase {ans['chase']:.2f}")
    score = round(100 * (ans["alive_6h"] + ans["organic"] + ans["flush"] + (1 - ans["chase"])) / 4)
    if score < s["min_score"]:
        failed.append(f"score {score}")
    return score, (not failed), failed


def build_state(scan, audit, pair, coin, maxc=3000):
    g = lambda k: F.get(pair, F.DEXSCREENER[k])  # noqa
    vals = {l["n"]: l["value"] for l in audit.get("layers", [])}
    age = int((time.time() - (coin.get("migrated_at") or time.time())) / 60)
    dev = (vals.get(3) or "n/a").split("%")[0]
    st = (f"Coin {scan.get('name')} on Solana, graduated {age} min ago. Liquidity ${money(g('liquidityUsd') or 0)}. "
          f"Market cap ${money(g('marketCap') or 0)}.\n"
          f"Last 5 min: {g('buys_m5')} buys, {g('sells_m5')} sells, volume ${money(g('volume_m5') or 0)}, price {g('priceChange_m5')}%.\n"
          f"Last 1h: {g('buys_h1')} buys, {g('sells_h1')} sells, volume ${money(g('volume_h1') or 0)}, price {g('priceChange_h1')}%.\n"
          f"Top 10 hold {(vals.get(4) or 'n/a').rstrip('%')}%. Dev holds {dev}%, sold 0. LP {vals.get(5)}. "
          f"Bundled {vals.get(7)}. Delegations {vals.get(8)}.")
    return st[:maxc]


class Analyst:
    name = "analyst"

    def __init__(self, desk):
        self.d = desk
        self.L = desk.log("analyst")
        self.asked = set()
        self.http = None

    async def run(self):
        while True:
            item = await self.d.bus["analyst"].get()
            while self.d.db.paused("analyst"):
                await asyncio.sleep(2)
            try:
                await self.analyse(item["scan"], item["audit"])
            except Exception as e:  # noqa
                self.L.info(f"{iso()} {item['scan'].get('mint')} crashed {type(e).__name__}: {e}")
            self.d.db.heartbeat("analyst", "")

    async def jev(self, state, s):
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            return None, "no answer", None
        if not self.http:
            self.http = aiohttp.ClientSession()
        body = {"model": "jev-latest", "state": state, "questions": QUESTIONS}
        t_end = time.time() + s["queue_429_s"]
        tries = 0
        while True:
            try:
                async with self.http.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"},
                                          timeout=aiohttp.ClientTimeout(total=s["timeout_s"])) as r:
                    if r.status == 429 and time.time() < t_end:
                        await asyncio.sleep(5)
                        continue
                    if r.status != 200:
                        raise RuntimeError(f"HTTP {r.status}")
                    j = await r.json(content_type=None)
                    return j, None, None
            except Exception:  # noqa
                tries += 1
                if tries >= 2:
                    return None, "no answer", None
                await asyncio.sleep(s["retry_gap_s"])

    async def analyse(self, scan, audit, write=True):
        db, mint = self.d.db, scan["mint"]
        S = self.d.s
        s, mode = S["analyst"], S["general"]["analyst_mode"]
        if mint in self.asked:
            return None  # never re-asks the same coin
        self.asked.add(mint)
        coin = db.one("SELECT * FROM coins WHERE mint=?", mint) or {}
        name = scan.get("name") or mint[:6]
        pairs = await self.d.src.dex_tokens([mint])
        pair = self.d.src.pumpswap_pair(pairs.get(mint, []))
        card = self.d.live.setdefault("analyst", {})
        card.update(name=name, score=None)
        failed, model, tokens, ans = [], mode, 0, None
        state = build_state(scan, audit, pair or {}, coin, s["state_max_chars"]) if pair else ""
        if not pair:
            failed = ["no answer"]
        elif mode == "rules":
            ans, model = rules_answers(pair, audit.get("bundled"), S["analyst_rules"]), "rules"
        else:
            j, err, _ = await self.jev(state, s)
            if err:
                failed = [err]
            else:
                model, tokens = j.get("model"), F.get(j, "usage.input_tokens") or F.get(j, "usage.inputTokens") or 0
                try:
                    ans = {k: float(F.get(j, f"answers.{k}.noul")) for k in QUESTIONS}
                    if any(not (0 <= v <= 1) for v in ans.values()):
                        raise ValueError
                except (TypeError, ValueError):
                    self.L.info(f"{iso()} {mint} bad answer raw={json.dumps(j)[:1000]}")
                    failed, ans = ["bad answer"], None
        score, passed = 0, False
        if ans is not None:
            score, passed, failed = gate(ans, s)
        out = {"agent": "analyst", "mint": mint, "answers": ans, "score": score, "passed": passed,
               "failed": failed, "model": model, "inputTokens": tokens}
        card.update(score=score, passed=passed)
        self.L.info(f"{iso()} {mint} state={hashlib.sha256(state.encode()).hexdigest()[:12]} {ans} score={score} "
                    f"{'PASS' if passed else 'REJECT ' + ','.join(failed)} tokens={tokens}")
        if not write:
            return out
        db.x("INSERT INTO analyses(ts,mint,passed,json) VALUES(?,?,?,?)", time.time(), mint, int(passed), json.dumps(out))
        db.decision("analyst", mint, name, "pass" if passed else "reject", ", ".join(failed) or f"score {score}")
        self.d.event("verdict", {"mint": mint, "score": score, "passed": passed})
        if passed:
            self.d.bus["executor"].put_nowait({"scan": scan, "audit": audit, "analyst": out})
        else:
            who = "Analyst (rules)" if model == "rules" else "Analyst"
            db.activity("reject", f"Rejected {name}", f"{who} · {failed[0]}", "Skipped", mint, 0)
        return out
