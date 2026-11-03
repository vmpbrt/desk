import re, pathlib
from desk import links

M = "FK5tP4Zw7ujWzaz3TgeJ5NcqGe5LFufo7m6zH6YQpump"


def test_coin_url_solana():
    assert links.coin_url(M, "solana") == f"https://fomo.family/coin?address={M}&chainId=1399811149&r=savipww&source=share_link"


def test_chain_ids():
    for ch, cid in {"solana": 1399811149, "robinhood": 4663, "base": 8453, "bnb": 56}.items():
        assert f"chainId={cid}&r=savipww" in links.coin_url("x", ch)


def test_unknown_chain_falls_back():
    assert links.coin_url(M, "dogechain") == links.SIGNUP_URL == "https://fomo.family/r/savipww"


def test_profile():
    assert links.profile_url("hkunseng") == "https://fomo.family/profile/hkunseng?source=share_link&r=savipww"


def test_no_hand_built_fomo_urls():
    root = pathlib.Path(__file__).resolve().parents[1]
    bad = []
    for p in list((root / "desk").glob("*.py")) + list((root / "web").glob("*")):
        if p.name == "links.py":
            continue
        if "fomo.family" in p.read_text():
            bad.append(p.name)
    assert not bad, f"FOMO URLs built outside links.py: {bad}"
