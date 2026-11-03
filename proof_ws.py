import asyncio, json, websockets, time
async def main():
    msgs=[]
    async with websockets.connect("wss://pumpportal.fun/api/data") as ws:
        await ws.send(json.dumps({"method":"subscribeMigration"}))
        t0=time.time()
        while time.time()-t0<600 and len([m for m in msgs if 'mint' in m])<3:
            try: raw=await asyncio.wait_for(ws.recv(), timeout=600-(time.time()-t0))
            except asyncio.TimeoutError: break
            m=json.loads(raw); msgs.append(m); print(time.strftime('%H:%M:%S'), raw[:300], flush=True)
    json.dump(msgs, open("logs/raw/pumpportal_migration_all.json","w"), indent=2)
    real=[m for m in msgs if 'mint' in m]
    if real: json.dump(real[0], open("logs/raw/pumpportal_migration.json","w"), indent=2)
    print("DONE", len(msgs), len(real))
asyncio.run(main())
