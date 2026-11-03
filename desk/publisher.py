"""Plan B publisher: encrypted JSON -> publish/site/data/*.bin every write_s; orphan gh-pages force-push
(single commit) at most every push_s. Token only via GIT_ASKPASS reading $GH_DESK_TOKEN; never in a URL/config."""
import asyncio, json, os, shutil, subprocess, time
from . import crypto, links
from .core import ROOT, iso, night_start_ts, night_id
from .executor import Executor  # noqa (type ref)
from .risk import exit_line

REPO = "https://github.com/vmpbrt/desk.git"
SITE = os.path.join(ROOT, "publish", "site")
WEB = os.path.join(ROOT, "web")
ASKPASS = os.path.join(ROOT, "scripts", "git_askpass.sh")
AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"]
RANGES = {"1H": 3600, "6H": 6 * 3600, "24H": 86400, "ALL": None}


def candles(rows, step=60):
    out = {}
    for r in rows:
        b = int(r["ts"] // step * step)
        c = out.get(b)
        p = r["price"]
        if not c:
            out[b] = {"t": b, "o": p, "h": p, "l": p, "c": p, "v": r["vol_m5"] or 0}
        else:
            c["h"], c["l"], c["c"] = max(c["h"], p), min(c["l"], p), p
            c["v"] = r["vol_m5"] or c["v"]
    return list(out.values())[-240:]


class Publisher:
    def __init__(self, desk, key, site=SITE):
        self.d, self.key, self.site = desk, key, site
        self.L = desk.log("publisher")
        self.last_push = 0
        self.last_push_ok = None
        self.pushes = 0

    # ---------- payloads ----------
    def night_base(self):
        db = self.d.db
        r = db.one("SELECT value FROM balance WHERE ts>=? ORDER BY ts LIMIT 1", night_start_ts())
        return r["value"] if r else db.balance_now()

    def agent_cards(self):
        db, live, now = self.d.db, self.d.live, time.time()
        hb = {r["agent"]: r for r in db.q("SELECT * FROM heartbeats")}
        ns = night_start_ts()
        cards = []
        for a in AGENTS:
            n = db.one("SELECT COUNT(*) n FROM decisions WHERE agent=? AND ts>=?", a, ns)["n"]
            h = hb.get(a)
            cards.append({"name": a, "paused": db.paused(a), "today": n,
                          "alive": bool(h and now - h["ts"] < 180), "heartbeat": h["ts"] if h else None,
                          "live": live.get(a, {})})
        return cards

    def positions(self):
        db, s = self.d.db, self.d.s
        out = []
        for p in db.open_positions():
            last = p["last_price"] or p["entry"]
            left = max(0, p["opened_at"] + s["risk"]["timeout_h"] * 3600 - time.time())
            out.append({"id": p["id"], "mint": p["mint"], "name": p["name"], "symbol": p["symbol"], "size": p["size"],
                        "entry": p["entry"], "last": last, "pct": round(100 * (last / p["entry"] - 1), 2),
                        "status": p["status"], "mode": p["mode"], "exitLine": exit_line(p, s["risk"]),
                        "trailActive": bool(p["trail_active"]), "timeLeftS": int(left),
                        "link": links.coin_url(p["mint"], "solana")})
        return out

    def activity_rows(self, limit, offset=0):
        rows = self.d.db.q("SELECT * FROM activity ORDER BY id DESC LIMIT ? OFFSET ?", limit, offset)
        for r in rows:
            if r["mint"]:
                r["link"] = links.coin_url(r["mint"], "solana")
        return rows

    def state(self):
        db, s = self.d.db, self.d.s
        bal = db.balance_now()
        base = self.night_base() or bal
        hb = db.one("SELECT MAX(ts) t FROM heartbeats")["t"]
        return {"v": 1, "updated": time.time(), "updatedIso": iso(), "mode": s["general"]["mode"], "balance": bal,
                "nightBase": base, "todayPct": round(100 * (bal / base - 1), 2) if base else 0,
                "multiplier": round(bal / base, 3) if base else 1, "night": night_id(), "heartbeat": hb,
                "agents": self.agent_cards(), "positions": self.positions(), "recent": self.activity_rows(5),
                "events": self.d.events[-50:], "lastPush": self.last_push_ok,
                "coins": [{"mint": c["mint"], "name": c["name"], "symbol": c["symbol"]} for c in self.coin_list()]}

    def chart(self, rng):
        db, now = self.d.db, time.time()
        sec = RANGES[rng]
        since = now - sec if sec else 0
        rows = db.q("SELECT ts,value FROM balance WHERE ts>=? ORDER BY ts", since)
        if len(rows) > 400:
            k = len(rows) // 400 + 1
            rows = rows[::k] + [rows[-1]]
        marks = [{"t": p["opened_at"], "kind": "buy", "label": p["name"]} for p in
                 db.q("SELECT * FROM positions WHERE opened_at>=?", since)]
        marks += [{"t": p["closed_at"], "kind": "sell" if (p["pnl"] or 0) >= 0 else "stop", "label": p["name"]} for p in
                  db.q("SELECT * FROM positions WHERE closed_at>=?", since)]
        return {"range": rng, "points": [[r["ts"], r["value"]] for r in rows], "base": rows[0]["value"] if rows else None,
                "marks": marks}

    def coin_list(self):
        db, ns = self.d.db, night_start_ts() - 86400
        return db.q("""SELECT * FROM coins WHERE mint IN (SELECT mint FROM positions) OR
                       (status IN ('passed') AND first_seen>=?) OR mint IN (SELECT mint FROM audits WHERE ts>=?)
                       ORDER BY first_seen DESC LIMIT 60""", ns, ns)

    def coin(self, c):
        db, mint = self.d.db, c["mint"]
        pair = self.d.last_pair.get(mint) or {}
        rows = db.q("SELECT ts,price,vol_m5 FROM prices WHERE mint=? AND ts>=? ORDER BY ts", mint, time.time() - 6 * 3600)
        pos = db.one("SELECT * FROM positions WHERE mint=? ORDER BY id DESC LIMIT 1", mint)
        au = db.one("SELECT json FROM audits WHERE mint=? ORDER BY id DESC LIMIT 1", mint)
        au = json.loads(au["json"]) if au else None
        last = rows[-1]["price"] if rows else None
        s = self.d.s
        posd = None
        if pos:
            posd = {k: pos[k] for k in ("id", "entry", "size", "peak", "status", "exit_reason", "exit_price", "pnl", "pct",
                                        "fees", "opened_at", "closed_at", "mode", "trail_active", "stop_pct", "trail_pct")}
            posd["exitLine"] = exit_line(pos, s["risk"])
            posd["timeLeftS"] = int(max(0, pos["opened_at"] + s["risk"]["timeout_h"] * 3600 - time.time()))
            posd["sinceEntryPct"] = round(100 * ((last or pos["entry"]) / pos["entry"] - 1), 2)
        return {"mint": mint, "name": c["name"], "symbol": c["symbol"], "chain": "solana", "logo": c.get("logo"),
                "ageH": round((time.time() - (c["migrated_at"] or time.time())) / 3600, 1), "price": last,
                "candles": candles(rows), "stats": {"mcap": pair.get("marketCap"), "liq": (pair.get("liquidity") or {}).get("usd"),
                                                     "vol24": (pair.get("volume") or {}).get("h24"), "holders": c["holders"]},
                "position": posd, "audit": {"passed": au["passed"], "checked": au["layersChecked"]} if au else None,
                "link": links.coin_url(mint, "solana")}

    def audit(self, c):
        au = self.d.db.one("SELECT json FROM audits WHERE mint=? ORDER BY id DESC LIMIT 1", c["mint"])
        if not au:
            return None
        j = json.loads(au["json"])
        j.update(name=c["name"], symbol=c["symbol"], link=links.coin_url(c["mint"], "solana"))
        return j

    def agent_detail(self, a):
        db = self.d.db
        rows = db.q("SELECT * FROM decisions WHERE agent=? ORDER BY id DESC LIMIT 30", a)
        for r in rows:
            r["link"] = links.coin_url(r["mint"], "solana") if r["mint"] else None
        hb = db.one("SELECT * FROM heartbeats WHERE agent=?", a)
        return {"name": a, "paused": db.paused(a), "heartbeat": hb, "decisions": rows,
                "today": db.one("SELECT COUNT(*) n FROM decisions WHERE agent=? AND ts>=?", a, night_start_ts())["n"],
                "settings": self.d.s.get(a, {})}

    def account(self):
        s = self.d.s
        g = s["general"]
        return {"mode": g["mode"], "stack": g["stack"], "paperStart": g["paper_start"], "balance": self.d.db.balance_now(),
                "fomoHandle": g["fomo_handle"], "profileUrl": links.profile_url(g["fomo_handle"]),
                "signupUrl": links.SIGNUP_URL, "notifier": g["notifier"], "telegram": bool(self.d.notifier and self.d.notifier.chat_id),
                "webPush": "Not available in plan B · alerts come on Telegram", "analystMode": g["analyst_mode"],
                "feePct": g["fee_pct"], "inviteUrl": links.SIGNUP_URL}

    def settings_doc(self):
        from . import config
        ch = self.d.db.q("SELECT * FROM change_log ORDER BY id DESC LIMIT 30")
        return {"settings": self.d.s, "ranges": {k: list(v) for k, v in config.RANGES.items()}, "changes": ch,
                "readOnly": True, "howto": "Change settings in Telegram: set group.key value"}

    def build(self):
        docs = {"state": self.state(), "account": self.account(), "settings": self.settings_doc()}
        for r in RANGES:
            docs[f"chart_{r}"] = self.chart(r)
        n = self.d.db.one("SELECT COUNT(*) n FROM activity")["n"]
        for pg in range(max(1, min(10, (n + 29) // 30))):
            rows = self.activity_rows(30, pg * 30)
            docs[f"activity_{pg}"] = {"page": pg, "rows": rows, "next": pg + 1 if (pg + 1) * 30 < min(n, 300) else None}
        for a in AGENTS:
            docs[f"agents_{a}"] = self.agent_detail(a)
        for c in self.coin_list():
            docs[f"coin_{c['mint']}"] = self.coin(c)
            au = self.audit(c)
            if au:
                docs[f"audit_{c['mint']}"] = au
        return docs

    # ---------- write + push ----------
    def write(self):
        docs = self.build()
        data = os.path.join(self.site, "data.new")
        shutil.rmtree(data, ignore_errors=True)
        os.makedirs(data)
        for name, doc in docs.items():
            with open(os.path.join(data, crypto.file_name(self.key, name)), "wb") as f:
                f.write(crypto.encrypt(self.key, json.dumps(doc, default=str).encode()))
        for fn in os.listdir(WEB):
            src = os.path.join(WEB, fn)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(self.site, fn))
        open(os.path.join(self.site, ".nojekyll"), "w").close()
        old = os.path.join(self.site, "data")
        shutil.rmtree(old, ignore_errors=True)
        os.rename(data, old)
        return len(docs)

    def _git(self, *args, check=True):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/home/box"),
               "GIT_ASKPASS": ASKPASS, "GIT_TERMINAL_PROMPT": "0", "GH_DESK_TOKEN": os.environ.get("GH_DESK_TOKEN", "")}
        r = subprocess.run(["git", "-c", "credential.helper=", "-c", "user.name=desk", "-c", "user.email=desk@localhost",
                            *args], cwd=self.site, env=env, capture_output=True, text=True, timeout=120)
        if check and r.returncode:
            raise RuntimeError(f"git {args[0]} failed: {r.stderr[-300:].replace(env['GH_DESK_TOKEN'] or 'x'*99, '***')}")
        return r

    def push(self):
        g = os.path.join(self.site, ".git")
        shutil.rmtree(g, ignore_errors=True)
        self._git("init", "-q", "-b", "gh-pages")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", f"publish {iso()}")
        self._git("push", "-q", "-f", REPO, "HEAD:gh-pages")
        self.last_push_ok = time.time()
        self.pushes += 1

    async def run(self):
        os.makedirs(self.site, exist_ok=True)
        while True:
            p = self.d.s["publish"]
            try:
                n = self.write()
                if time.time() - self.last_push >= p["push_s"]:
                    self.last_push = time.time()
                    await asyncio.to_thread(self.push)
                    self.L.info(f"{iso()} pushed {n} files")
            except Exception as e:  # noqa
                self.L.info(f"{iso()} publish error {type(e).__name__}: {e}")
            self.d.db.heartbeat("publisher", f"pushes={self.pushes}")
            await asyncio.sleep(p["write_s"])
