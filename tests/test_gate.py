from desk.analyst import rules_answers, gate
from desk import config

S = config.defaults()


def pair(vol_h1=30000, buys_h1=200, m5=-6, buys_m5=20, sells_m5=10, h1=10):
    return {"volume": {"h1": vol_h1, "m5": 1000}, "txns": {"h1": {"buys": buys_h1, "sells": 50}, "m5": {"buys": buys_m5, "sells": sells_m5}},
            "priceChange": {"m5": m5, "h1": h1}}


def test_rules_strong_coin_passes():
    a = rules_answers(pair(), 0, S["analyst_rules"])
    assert a == {"alive_6h": 1.0, "organic": 1.0, "flush": 1.0, "chase": 0.125}
    score, ok, failed = gate(a, S["analyst"])
    assert ok and score == 97 and failed == []


def test_bundles_halve_organic():
    a = rules_answers(pair(), 3, S["analyst_rules"])
    assert a["organic"] == 0.5
    _, ok, failed = gate(a, S["analyst"])
    assert not ok and failed[0].startswith("organic")


def test_chase_rejects():
    a = rules_answers(pair(h1=60), 0, S["analyst_rules"])
    assert a["chase"] == 0.75
    _, ok, failed = gate(a, S["analyst"])
    assert not ok and any(f.startswith("chase") for f in failed)


def test_thin_volume_rejects_alive():
    a = rules_answers(pair(vol_h1=5000), 0, S["analyst_rules"])
    _, ok, failed = gate(a, S["analyst"])
    assert not ok and failed[0].startswith("alive_6h")


def test_score_floor():
    a = {"alive_6h": .70, "organic": .65, "flush": 0, "chase": .30}
    score, ok, failed = gate(a, S["analyst"])
    assert score == 51 and not ok and failed == ["score 51"]


def test_flush_partial():
    assert rules_answers(pair(m5=-2), 0, S["analyst_rules"])["flush"] == 0.5
    assert rules_answers(pair(m5=3), 0, S["analyst_rules"])["flush"] == 0.0
    assert rules_answers(pair(m5=-6, buys_m5=5, sells_m5=10), 0, S["analyst_rules"])["flush"] == 0.5
