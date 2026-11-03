"""Field map built from raw samples in logs/raw/ (captured 2026-11-03).
Every path was observed in a real response. None = key not present in the source; never guess.
Path syntax: dotted keys; "[]" = iterate a list (caller does it); "[0]" = index.
"""

MIGRATION = {  # PumpPortal subscribeMigration message (logs/raw/pumpportal_migration.json)
    "mint": "mint",
    "signature": "signature",
    "txType": "txType",          # "migrate"
    "pool": None,                # message has "pool":"pump-amm" = a venue LABEL, not an address.
                                 # Pool address comes from DexScreener pairAddress (dexId pumpswap)
                                 # or RugCheck markets[].pubkey (marketType pump_fun_amm).
    "pool_label": "pool",
    "creator": None,             # not in message -> RUGCHECK["creator"]
}

DEXSCREENER = {  # GET /tokens/v1/solana/{mints} -> list of pairs; paths are per pair object
    "pairAddress": "pairAddress",
    "dexId": "dexId",            # "pumpswap" after graduation; "pumpfun" = still the curve pair
    "priceUsd": "priceUsd",      # string
    "liquidityUsd": "liquidity.usd",   # absent on pumpfun curve pairs
    "marketCap": "marketCap",
    "volume_m5": "volume.m5", "volume_h1": "volume.h1", "volume_h24": "volume.h24",
    "buys_m5": "txns.m5.buys", "sells_m5": "txns.m5.sells",
    "buys_h1": "txns.h1.buys", "sells_h1": "txns.h1.sells",
    "priceChange_m5": "priceChange.m5", "priceChange_h1": "priceChange.h1",
    "priceChange_h24": "priceChange.h24",
    "pairCreatedAt": "pairCreatedAt",  # epoch ms
    "baseAddress": "baseToken.address",
    "name": "baseToken.name", "symbol": "baseToken.symbol",
}

RUGCHECK_REPORT = {  # GET /v1/tokens/{mint}/report
    "creator": "creator",
    "creatorBalance": "creatorBalance",     # raw amount (0 in both samples)
    "tokenProgram": "tokenProgram",
    "mintAuthority": "mintAuthority", "freezeAuthority": "freezeAuthority",
    "supply": "token.supply", "decimals": "token.decimals",
    "totalHolders": "totalHolders",
    "markets": "markets",                    # list
    "market_pool": "markets[].pubkey",       # == DexScreener pairAddress for the pumpswap pair
    "market_type": "markets[].marketType",   # "pump_fun_amm" (PumpSwap) / "pump_fun" (curve)
    "market_lpLockedPct": "markets[].lp.lpLockedPct",
    "market_lpLocked": "markets[].lp.lpLocked",
    "market_lpUnlocked": "markets[].lp.lpUnlocked",
    "market_lpBurnedPct": None,              # no burned key exists; only lpLockedPct
    "topHolders": "topHolders",              # list of 20
    "topHolder_owner": "topHolders[].owner",
    "topHolder_address": "topHolders[].address",
    "topHolder_pct": "topHolders[].pct",
    "topHolder_amount": "topHolders[].amount",
    "topHolder_insider": "topHolders[].insider",
    "graphInsidersDetected": "graphInsidersDetected",
}

RUGCHECK_GRAPH = {  # GET /v1/tokens/{mint}/insiders/graph -> LIST of networks ([] when none)
    "networks": "",                          # top-level list
    "network_type": "[].network_type",       # "transfer" seen
    "nodes": "[].nodes",
    "node_wallet": "[].nodes[].id",
    "node_holdings": "[].nodes[].holdings",  # holder flag = holdings > 0
    "node_participant": "[].nodes[].participant",
    "edges": "[].links",                     # key is "links", NOT "edges"
    "edge_source": "[].links[].source",
    "edge_target": "[].links[].target",
    "holder_flag": None,                     # no explicit flag; use holdings > 0
}

RPC = {
    "mintAuthority": "result.value.data.parsed.info.mintAuthority",      # getAccountInfo jsonParsed
    "freezeAuthority": "result.value.data.parsed.info.freezeAuthority",
    "supply_acct": "result.value.data.parsed.info.supply",               # string
    "decimals_acct": "result.value.data.parsed.info.decimals",
    "programOwner": "result.value.owner",                                 # Token vs Token-2022
    "supply": "result.value.amount",                                      # getTokenSupply
    "supply_decimals": "result.value.decimals",
    "largest_accounts": None,  # getTokenLargestAccounts NOT USED: public RPC returns 429 every time.
    # DECISION (user, 2026-11-03): Top 10 holders (Scanner kill + Auditor layer 4) is computed from the
    # layer-8 getProgramAccounts results (Token + Token-2022): group by gpa_owner, sum gpa_amount,
    # EXCLUDE accounts whose owner is the pool (DexScreener pairAddress) or the bonding curve,
    # take the 10 largest, divide by supply.
    # getProgramAccounts jsonParsed, per item:
    "gpa_pubkey": "pubkey",
    "gpa_owner": "account.data.parsed.info.owner",
    "gpa_mint": "account.data.parsed.info.mint",
    "gpa_amount": "account.data.parsed.info.tokenAmount.amount",
    "gpa_delegate": "account.data.parsed.info.delegate",  # key only present when set (none in sample)
}

_MISSING = object()


def get(obj, path, default=None):
    """Follow a dotted path ('a.b.0.c' or 'a.b[0].c'). Missing key/index -> default. path None -> default."""
    if path is None:
        return default
    if path == "":
        return obj
    cur = obj
    for part in path.replace("[", ".").replace("]", "").split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(cur, dict):
            cur = cur.get(part, _MISSING)
            if cur is _MISSING:
                return default
        else:
            return default
    return cur
