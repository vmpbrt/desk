"""FOMO fees. ASSUMPTION (master prompt part 1/3 text not available to the builder):
trading fee 0.50% per side, minus 10% referral discount (signed up via r=savipww) = 0.45% per side.
Solana gas covered by FOMO -> 0. Value is settings key general.fee_pct so it can be corrected."""


def fee(size_usd, fee_pct=0.45):
    return round(size_usd * fee_pct / 100.0, 6)


def round_trip(size_in, size_out, fee_pct=0.45):
    """fees on both sides: buy notional and sell notional"""
    return fee(size_in, fee_pct) + fee(size_out, fee_pct)


def paper_pnl(size_usd, entry, exit_price, fee_pct=0.45):
    """returns (pnl_usd after fees, pct after fees, fees_usd)"""
    gross_out = size_usd * (exit_price / entry)
    fees = round_trip(size_usd, gross_out, fee_pct)
    pnl = gross_out - size_usd - fees
    return round(pnl, 4), round(100 * pnl / size_usd, 2), round(fees, 4)
