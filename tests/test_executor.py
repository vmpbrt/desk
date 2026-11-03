from desk.executor import size_order
from desk import config

E = config.defaults()["executor"]


def test_8pct_of_stack():
    assert size_order(1240, 1240, 1e6, E) == (99.2, "stack_pct", None)


def test_liq_cap():
    s, cap, r = size_order(1240, 1240, 3000, E)  # 2% of 3000 = 60
    assert (s, cap) == (60.0, "liq_pct")


def test_bump_to_min_ticket_on_200_paper():
    s, cap, r = size_order(200, 200, 1e6, E)  # 8% = 16 -> bump to 47.50
    assert s == 47.5 and cap.endswith("+bump") and r is None


def test_bump_needs_free_cash():
    s, cap, r = size_order(200, 30, 1e6, E)
    assert s is None and "bump" in r


def test_skip_mode():
    s, cap, r = size_order(200, 200, 1e6, dict(E, below_min="skip"))
    assert s is None and r == "below min ticket"


def test_no_free_stack():
    s, cap, r = size_order(1240, 50, 1e6, E)
    assert s is None and r == "no free stack"
