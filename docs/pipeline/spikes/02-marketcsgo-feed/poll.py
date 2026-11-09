import urllib.request, json, time, sys
prev=None
for i in range(8):
    t0=time.time()
    req=urllib.request.Request("https://market.csgo.com/api/v2/prices/USD.json",headers={"Accept-Encoding":"identity"})
    r=urllib.request.urlopen(req); raw=r.read(); dt=time.time()-t0
    open(f"out/poll{i}.json","wb").write(raw)
    d=json.loads(raw); cur={x["market_hash_name"]:(x["price"],x["volume"]) for x in d["items"]}
    chp=chv=0
    if prev:
        chp=sum(1 for k,v in cur.items() if k in prev and prev[k][0]!=v[0]); chv=sum(1 for k,v in cur.items() if k in prev and prev[k][1]!=v[1])
    print(i,time.strftime("%H:%M:%S"),"time=",d["time"],"items=",len(cur),"bytes=",len(raw),"resp_s=%.2f"%dt,"last-mod=",r.headers["last-modified"],"price_changed=",chp,"vol_changed=",chv,flush=True)
    prev=cur
    if i<7: time.sleep(150)
