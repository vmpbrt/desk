import json, sys, time, requests
RPC="https://api.mainnet-beta.solana.com"; mint=sys.argv[1]
def rpc(method,params,timeout=60):
    t=time.time()
    try:
        r=requests.post(RPC,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},timeout=timeout)
        return r.status_code, r.json(), time.time()-t
    except Exception as e: return None, {"exception":repr(e)}, time.time()-t
for i in range(5):
    s,j,dt=rpc("getTokenLargestAccounts",[mint])
    print("largest try",i,s,dt, json.dumps(j)[:200])
    if "result" in j: json.dump(j,open("logs/raw/rpc_getTokenLargestAccounts.json","w"),indent=2); break
    time.sleep(2*(i+1))
for name,prog,filters in [("token","TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",[{"dataSize":165},{"memcmp":{"offset":0,"bytes":mint}}]),
                          ("token2022","TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",[{"memcmp":{"offset":0,"bytes":mint}}])]:
    s,j,dt=rpc("getProgramAccounts",[prog,{"encoding":"jsonParsed","filters":filters}],timeout=90)
    if "result" in j:
        res=j["result"]; pos=[a for a in res if int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])>0]
        dels={}
        for a in res:
            d=a["account"]["data"]["parsed"]["info"].get("delegate")
            if d: dels[d]=dels.get(d,0)+1
        print(name,"HTTP",s,"accounts",len(res),"balance>0",len(pos),"delegates",dels,"secs",round(dt,1))
        json.dump(res[:3],open(f"logs/raw/rpc_getProgramAccounts_{name}_sample.json","w"),indent=2)
    else:
        print(name,"HTTP",s,"ERROR",json.dumps(j)[:500],"secs",round(dt,1))
        json.dump(j,open(f"logs/raw/rpc_getProgramAccounts_{name}_error.json","w"),indent=2)
