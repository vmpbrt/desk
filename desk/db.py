"""SQLite store: all tables of the desk. One connection, used from the single asyncio loop."""
import json, sqlite3, time, os
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS change_log(id INTEGER PRIMARY KEY, ts REAL, key TEXT, old TEXT, new TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS coins(mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, pool TEXT, signature TEXT,
  creator TEXT, creator_bal_raw TEXT, supply_raw TEXT, decimals INTEGER, migrated_at REAL, first_seen REAL,
  status TEXT, reason TEXT, liq_at_pass REAL, holders INTEGER, h24_max REAL, logo TEXT);
CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, ts REAL, mint TEXT, verdict TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY, ts REAL, mint TEXT, passed INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY, ts REAL, mint TEXT, passed INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, ts REAL, mint TEXT, status TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, mint TEXT, name TEXT, symbol TEXT, entry REAL,
  size REAL, peak REAL, stop_pct REAL, trail_pct REAL, trail_on_pct REAL, trail_active INTEGER DEFAULT 0,
  opened_at REAL, status TEXT, exit_reason TEXT, exit_price REAL, pnl REAL, pct REAL, fees REAL,
  closed_at REAL, mode TEXT, last_push_at REAL, resent INTEGER DEFAULT 0, no_price INTEGER DEFAULT 0,
  last_price REAL);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, title TEXT, subtitle TEXT,
  value TEXT, mint TEXT, positive INTEGER);
CREATE TABLE IF NOT EXISTS prices(mint TEXT, ts REAL, price REAL, vol_m5 REAL);
CREATE INDEX IF NOT EXISTS prices_mint_ts ON prices(mint, ts);
CREATE TABLE IF NOT EXISTS skips(id INTEGER PRIMARY KEY, ts REAL, mint TEXT, agent TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, ts REAL, agent TEXT, mint TEXT, name TEXT,
  verdict TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS push_subs(id INTEGER PRIMARY KEY, json TEXT);
CREATE TABLE IF NOT EXISTS balance(ts REAL, value REAL);
CREATE TABLE IF NOT EXISTS pushes(id INTEGER PRIMARY KEY, ts REAL, text TEXT, mint TEXT, ok INTEGER);
CREATE TABLE IF NOT EXISTS heartbeats(agent TEXT PRIMARY KEY, ts REAL, info TEXT);
CREATE TABLE IF NOT EXISTS agent_state(agent TEXT PRIMARY KEY, paused INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.c = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)
        if not self.c.execute("SELECT 1 FROM settings").fetchone():
            self.c.execute("INSERT INTO settings VALUES(1,?)", (json.dumps(config.defaults()),))
        for a in ("scanner", "auditor", "analyst", "executor", "risk"):
            self.c.execute("INSERT OR IGNORE INTO agent_state(agent,paused) VALUES(?,0)", (a,))
        if not self.c.execute("SELECT 1 FROM balance").fetchone():
            self.c.execute("INSERT INTO balance VALUES(?,?)", (time.time(), self.settings()["general"]["paper_start"]))

    # generic
    def q(self, sql, *a):
        return [dict(r) for r in self.c.execute(sql, a).fetchall()]

    def one(self, sql, *a):
        r = self.c.execute(sql, a).fetchone()
        return dict(r) if r else None

    def x(self, sql, *a):
        return self.c.execute(sql, a)

    # settings
    def settings(self):
        s = json.loads(self.c.execute("SELECT json FROM settings").fetchone()[0])
        d = config.defaults()  # forward-fill new keys
        for g, kv in d.items():
            s.setdefault(g, {})
            for k, v in kv.items():
                s[g].setdefault(k, v)
        return s

    def set_setting(self, key, value, source="telegram"):
        s = self.settings()
        g, k = key.split(".", 1)
        old = s[g][k]
        s[g][k] = value
        self.c.execute("UPDATE settings SET json=? WHERE id=1", (json.dumps(s),))
        self.c.execute("INSERT INTO change_log(ts,key,old,new,source) VALUES(?,?,?,?,?)",
                       (time.time(), key, json.dumps(old), json.dumps(value), source))
        return old

    def kv_get(self, k, default=None):
        r = self.one("SELECT v FROM kv WHERE k=?", k)
        return json.loads(r["v"]) if r else default

    def kv_set(self, k, v):
        self.x("INSERT OR REPLACE INTO kv VALUES(?,?)", k, json.dumps(v))

    def paused(self, agent):
        r = self.one("SELECT paused FROM agent_state WHERE agent=?", agent)
        return bool(r and r["paused"])

    def heartbeat(self, agent, info=""):
        self.x("INSERT OR REPLACE INTO heartbeats VALUES(?,?,?)", agent, time.time(), info)

    def activity(self, kind, title, subtitle, value, mint=None, positive=None):
        self.x("INSERT INTO activity(ts,kind,title,subtitle,value,mint,positive) VALUES(?,?,?,?,?,?,?)",
               time.time(), kind, title, subtitle, value, mint, positive)

    def decision(self, agent, mint, name, verdict, reason):
        self.x("INSERT INTO decisions(ts,agent,mint,name,verdict,reason) VALUES(?,?,?,?,?,?)",
               time.time(), agent, mint, name, verdict, reason)

    def price(self, mint, price, vol_m5):
        self.x("INSERT INTO prices VALUES(?,?,?,?)", mint, time.time(), price, vol_m5)

    # money
    def open_positions(self):
        return self.q("SELECT * FROM positions WHERE status IN ('open','exiting')")

    def balance_now(self):
        """PAPER: paper_start + closed pnl + marked-to-market open pnl. LIVE: stack + same."""
        s = self.settings()
        base = s["general"]["paper_start"] if s["general"]["mode"] == "PAPER" else s["general"]["stack"]
        mode = s["general"]["mode"]
        closed = self.one("SELECT COALESCE(SUM(pnl),0) p FROM positions WHERE status='closed' AND mode=?", mode)["p"]
        mtm = 0.0
        for p in self.open_positions():
            if p["mode"] == mode and p["last_price"] and p["entry"]:
                mtm += p["size"] * (p["last_price"] / p["entry"] - 1)
        return round(base + closed + mtm, 2)
