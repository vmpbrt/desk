"""STEP 10 settings: every [bracket] number from the master prompt, with allowed ranges.
ASSUMPTION: the exact STEP 10 range table (part 1/3) was not available to the builder; ranges
below are conservative and documented in ASSUMPTIONS.md. Values only change through Telegram
commands (plan B: Account tab is read-only), are validated here, and every change goes to change_log."""
import copy

DEFAULTS = {
    "general": {
        "mode": "PAPER", "stack": 200.0, "paper_start": 200.0,
        "rpc_url": "https://api.mainnet-beta.solana.com",
        "fomo_handle": "hkunseng", "analyst_mode": "rules", "notifier": "telegram",
        "fee_pct": 0.45,
    },
    "scanner": {
        "poll_s": 60, "min_liq_usd": 40000, "max_top10_pct": 25, "max_h1_pct": 80,
        "min_age_min": 10, "max_wait_min": 60, "runner_h24_pct": 300, "ws_silence_s": 120,
        "dex_rpm": 250, "dex_429_wait_s": 30, "enabled": True,
    },
    "auditor": {
        "enabled": True, "max_dev_pct": 5, "max_top10_pct": 25, "min_lp_pct": 99,
        "max_liq_drop_pct": 50, "min_age_min": 10, "max_bundle": 5, "max_delegate": 10,
        "retries": 3, "retry_gap_s": 2,
    },
    "analyst": {
        "min_alive": 0.70, "min_organic": 0.65, "max_chase": 0.30, "min_score": 70,
        "state_max_chars": 3000, "timeout_s": 10, "retry_gap_s": 2, "queue_429_s": 60,
        "enabled": True,
    },
    "analyst_rules": {
        "vol_h1_full": 25000, "buys_h1_full": 150, "bundle_factor": 0.5, "bundle_min": 3,
        "flush_drop_pct": 5, "chase_full_pct": 80,
    },
    "executor": {
        "stack_pct": 8, "liq_pct": 2, "min_ticket": 47.50, "below_min": "bump",
        "live_tap_min": 5, "enabled": True,
    },
    "risk": {
        "poll_s": 15, "stop_pct": 6, "trail_on_pct": 20, "trail_pct": 12, "timeout_h": 3,
        "timeout_min_gain_pct": 5, "resend_min": 2, "no_price_polls": 4, "backoff_429_s": 30,
        "enabled": True,
    },
    "publish": {"write_s": 15, "push_s": 360},
}

# (min, max) for numbers, tuple of choices for strings, bool for flags
RANGES = {
    "general.mode": ("PAPER", "LIVE"), "general.stack": (10, 1_000_000),
    "general.paper_start": (10, 1_000_000), "general.analyst_mode": ("rules", "jev"),
    "general.notifier": ("telegram",), "general.fee_pct": (0, 5),
    "scanner.poll_s": (30, 600), "scanner.min_liq_usd": (1000, 10_000_000),
    "scanner.max_top10_pct": (1, 100), "scanner.max_h1_pct": (1, 10000),
    "scanner.min_age_min": (0, 240), "scanner.max_wait_min": (5, 720),
    "scanner.runner_h24_pct": (10, 100000), "scanner.ws_silence_s": (30, 600),
    "scanner.dex_rpm": (10, 280), "scanner.dex_429_wait_s": (5, 300),
    "auditor.max_dev_pct": (0, 100), "auditor.max_top10_pct": (1, 100),
    "auditor.min_lp_pct": (0, 100), "auditor.max_liq_drop_pct": (1, 100),
    "auditor.min_age_min": (0, 240), "auditor.max_bundle": (1, 1000),
    "auditor.max_delegate": (1, 100000), "auditor.retries": (1, 10), "auditor.retry_gap_s": (1, 30),
    "analyst.min_alive": (0, 1), "analyst.min_organic": (0, 1), "analyst.max_chase": (0, 1),
    "analyst.min_score": (0, 100), "analyst.state_max_chars": (500, 3000),
    "analyst.timeout_s": (2, 60), "analyst.retry_gap_s": (1, 30), "analyst.queue_429_s": (5, 600),
    "analyst_rules.vol_h1_full": (100, 10_000_000), "analyst_rules.buys_h1_full": (1, 100000),
    "analyst_rules.bundle_factor": (0, 1), "analyst_rules.bundle_min": (1, 1000),
    "analyst_rules.flush_drop_pct": (0.1, 100), "analyst_rules.chase_full_pct": (1, 10000),
    "executor.stack_pct": (0.1, 100), "executor.liq_pct": (0.01, 100),
    "executor.min_ticket": (1, 100000), "executor.below_min": ("bump", "skip"),
    "executor.live_tap_min": (1, 60),
    "risk.poll_s": (5, 300), "risk.stop_pct": (0.5, 90), "risk.trail_on_pct": (1, 1000),
    "risk.trail_pct": (0.5, 90), "risk.timeout_h": (0.25, 48), "risk.timeout_min_gain_pct": (0, 1000),
    "risk.resend_min": (1, 60), "risk.no_price_polls": (1, 100), "risk.backoff_429_s": (5, 300),
    "publish.write_s": (5, 300), "publish.push_s": (60, 3600),
}
LOCKED = {"general.rpc_url", "general.fomo_handle"}  # changed via their own commands only


def defaults():
    return copy.deepcopy(DEFAULTS)


def flat(settings):
    return {f"{g}.{k}": v for g, d in settings.items() for k, v in d.items()}


def validate(key, raw, current):
    """returns (ok, value_or_error)"""
    if "." not in key:
        return False, "use group.key, e.g. risk.stop_pct"
    g, k = key.split(".", 1)
    if g not in current or k not in current[g]:
        return False, f"unknown setting {key}"
    old = current[g][k]
    if isinstance(old, bool):
        s = str(raw).lower()
        if s in ("on", "true", "1", "yes"):
            return True, True
        if s in ("off", "false", "0", "no"):
            return True, False
        return False, "use on/off"
    rng = RANGES.get(key)
    if isinstance(rng, tuple) and rng and isinstance(rng[0], str):
        v = str(raw)
        if key == "general.mode":
            v = v.upper()
        return (True, v) if v in rng else (False, f"{key} must be one of {', '.join(rng)}")
    if isinstance(old, (int, float)):
        try:
            v = float(str(raw).replace("$", "").replace("%", "").replace(",", ""))
        except ValueError:
            return False, f"{key} needs a number"
        if rng and not (rng[0] <= v <= rng[1]):
            return False, f"{key} must be between {rng[0]} and {rng[1]}"
        if isinstance(old, int) and not isinstance(old, bool) and v.is_integer():
            v = int(v)
        return True, v
    if isinstance(old, str):
        return True, str(raw)
    return False, "not editable"
