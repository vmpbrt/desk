import time
from desk.risk import evaluate, exit_line
from desk import config, fees

R = config.defaults()["risk"]


def pos(**kw):
    p = {"entry": 1.0, "peak": 1.0, "stop_pct": 6, "trail_pct": 12, "trail_on_pct": 20, "trail_active": 0,
         "opened_at": time.time()}
    p.update(kw)
    return p


def test_hold():
    assert evaluate(pos(), 1.02, time.time(), R)[1] is None


def test_stop_at_minus_6():
    assert evaluate(pos(), 0.94, time.time(), R)[1] == "stop"
    assert evaluate(pos(), 0.9401, time.time(), R)[1] is None


def test_trail_arms_at_20_and_exits_12_below_peak():
    p = pos()
    upd, r = evaluate(p, 1.20, time.time(), R)
    assert r is None and upd["trail_active"] == 1
    p.update(upd)
    upd, r = evaluate(p, 1.50, time.time(), R); p.update(upd)
    assert r is None and p["peak"] == 1.50
    assert evaluate(p, 1.33, time.time(), R)[1] is None
    assert evaluate(p, 1.32, time.time(), R)[1] == "trail"


def test_timeout():
    old = time.time() - 3 * 3600 - 1
    assert evaluate(pos(opened_at=old), 1.04, time.time(), R)[1] == "timeout"
    assert evaluate(pos(opened_at=old), 1.06, time.time(), R)[1] is None


def test_stop_never_loosens():
    loose = dict(R, stop_pct=10)
    assert evaluate(pos(), 0.93, time.time(), loose)[1] == "stop"   # position keeps its 6%
    tight = dict(R, stop_pct=4)
    upd, r = evaluate(pos(), 0.95, time.time(), tight)
    assert r == "stop" and upd["stop_pct"] == 4


def test_exit_line():
    assert abs(exit_line(pos(), R) - 0.94) < 1e-9
    assert abs(exit_line(pos(trail_active=1, peak=2.0), R) - 1.76) < 1e-9


def test_fees_paper_pnl():
    pnl, pct, fee = fees.paper_pnl(50, 1.0, 1.32, 0.45)
    assert fee == round(50 * .0045 + 66 * .0045, 4) or abs(fee - (0.225 + 0.297)) < 1e-6
    assert abs(pnl - (16 - (0.225 + 0.297))) < 1e-6
