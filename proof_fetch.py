import json, sys, requests
mint=sys.argv[1]; tag=sys.argv[2] if len(sys.argv)>2 else ""
RPC="https://api.mainnet-beta.solana.com"
def save(name,obj): json.dump(obj,open(f"logs/raw/{name}{tag}.json","w"),indent=2)
def rpc(method,params):
    return requests.post(RPC,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},timeout=30).json()
r=requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{mint}",timeout=20); save("dexscreener_pair",r.json()); print("dex",r.status_code,len(r.json()) if isinstance(r.json(),list) else r.text[:200])
for n,u in [("rugcheck_report",f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"),("rugcheck_insiders_graph",f"https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph")]:
    r=requests.get(u,timeout=30)
    try: j=r.json()
    except Exception: j={"_status":r.status_code,"_text":r.text[:2000]}
    save(n,j); print(n,r.status_code,type(j).__name__, (list(j.keys())[:40] if isinstance(j,dict) else len(j)))
for n,m,p in [("rpc_getAccountInfo","getAccountInfo",[mint,{"encoding":"jsonParsed"}]),("rpc_getTokenSupply","getTokenSupply",[mint]),("rpc_getTokenLargestAccounts","getTokenLargestAccounts",[mint])]:
    j=rpc(m,p); save(n,j); print(n, json.dumps(j)[:300])
