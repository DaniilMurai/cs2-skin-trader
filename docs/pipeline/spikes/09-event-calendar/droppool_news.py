import json,datetime,re,urllib.request,urllib.parse
U="https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
f=lambda t:datetime.datetime.fromtimestamp(t,datetime.UTC).date()
allit={};end=None
for _ in range(10):
    p=dict(appid=730,count=1000,maxlength=0,feeds='steam_community_announcements')
    if end:p['enddate']=end
    r=json.load(urllib.request.urlopen(urllib.request.Request(U+'?'+urllib.parse.urlencode(p),headers={'User-Agent':'cs2-skin-trader-research/0.1'}),timeout=90))['appnews']['newsitems']
    new=[i for i in r if i['gid'] not in allit]
    if not new:break
    for i in new:allit[i['gid']]=i
    end=min(i['date'] for i in new)
print('official posts',len(allit),min(f(i['date']) for i in allit.values()))
pat=re.compile(r'drop pool|active duty|rare item (drop|special)|Weapon Case|Souvenir|Operation|trade[- ]?up|Trade Protection|Trade-?up contract|Sticker Capsule',re.I)
cats={'drop pool':r'drop pool|active duty|no longer drop|now drops?|drop(s)? (from|in)','case/capsule':r'is now available|new (weapon )?case|Capsule is','operation':r'operation','tradeup':r'trade[- ]?up','tradeprot':r'trade protect|tradable after'}
for k,v in cats.items():
    m=sorted((f(i['date']),i['contents'][:0]+re.sub(r'\s+',' ',(re.search(v,i['contents'],re.I) and i['contents'][max(0,re.search(v,i['contents'],re.I).start()-40):re.search(v,i['contents'],re.I).start()+90]) or '')) for i in allit.values() if re.search(v,i['contents'],re.I))
    print('\n##',k,len(m))
    for x in (m[:3]+m[-6:]): print(x)
print('\n#### care package / terminal / case drops')
for i in sorted(allit.values(),key=lambda i:i['date']):
    c=i['contents']
    if f(i['date'])>=datetime.date(2023,1,1) and re.search(r'care package|Terminal|rotat|no longer (be )?(drop|available)|will (now )?drop|weapon cases? (will|can|no longer)|case drop',c,re.I):
        m=re.search(r'care package|Terminal|rotat|no longer (be )?(drop|available)|will (now )?drop|weapon cases? (will|can|no longer)|case drop',c,re.I)
        print(f(i['date']),re.sub(r'\s+',' ',c[max(0,m.start()-80):m.start()+160]))
