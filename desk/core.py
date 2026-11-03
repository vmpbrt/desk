"""Shared context for the 5 agents: db, sources, notifier, bus, per-agent logs."""
import asyncio, datetime, logging, os, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iso(ts=None):
    return datetime.datetime.fromtimestamp(ts or time.time(), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def night_id(ts=None):
    """A night runs 12:00 ET to 12:00 ET (ASSUMPTION). 'same night' rules key off this."""
    d = datetime.datetime.fromtimestamp(ts or time.time(), ET) - datetime.timedelta(hours=12)
    return d.strftime("%Y-%m-%d")


def night_start_ts(ts=None):
    d = datetime.datetime.strptime(night_id(ts), "%Y-%m-%d").replace(tzinfo=ET) + datetime.timedelta(hours=12)
    return d.timestamp()


def agent_logger(name, logdir=None):
    logdir = logdir or os.path.join(ROOT, "logs")
    os.makedirs(logdir, exist_ok=True)
    lg = logging.getLogger("agent." + name)
    if not lg.handlers:
        h = logging.FileHandler(os.path.join(logdir, f"{name}.log"))
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    return lg


def short(addr):
    return f"{addr[:4]}...{addr[-3:]}" if addr and len(addr) > 10 else (addr or "")


def money(v):
    v = float(v)
    if abs(v) >= 1e6:
        return f"{v/1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"{v/1e3:.1f}K"
    return f"{v:,.2f}"


class Desk:
    def __init__(self, db, sources, notifier, logdir=None):
        self.db, self.src, self.notifier = db, sources, notifier
        self.bus = {"auditor": asyncio.Queue(), "analyst": asyncio.Queue(), "executor": asyncio.Queue()}
        self.logdir = logdir
        self.live = {}  # live card state per agent for the app
        self.last_pair = {}  # mint -> latest DexScreener pair
        self.events = []  # recent SSE-like events (plan B: published in state)

    @property
    def s(self):
        return self.db.settings()

    def log(self, agent):
        return agent_logger(agent, self.logdir)

    def event(self, kind, data):
        self.events.append({"t": time.time(), "type": kind, **data})
        self.events = self.events[-200:]
