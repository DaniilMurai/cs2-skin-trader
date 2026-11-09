import datetime,json,collections,urllib.request,urllib.parse
def get(u,**p):
    r=urllib.request.urlopen(urllib.request.Request(u+'?'+urllib.parse.urlencode(p),headers={'User-Agent':'cs2-skin-trader-research/0.1'}),timeout=60); b=r.read(); print('  HTTP',r.status,len(b)); return json.loads(b)
U="https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
n=get(U,appid=730,count=1000,maxlength=50)['appnews']; it=n['newsitems']; print('count',n.get('count'),len(it))
ds=sorted(i['date'] for i in it); f=lambda t:datetime.datetime.fromtimestamp(t,datetime.UTC).date()
print('range',f(ds[0]),f(ds[-1]))
print(collections.Counter(i['feedname'] for i in it))
# paging via enddate
allit={i['gid']:i for i in it}; end=ds[0]
for _ in range(60):
    r=get(U,appid=730,count=1000,maxlength=50,enddate=end)['appnews']['newsitems']
    new=[i for i in r if i['gid'] not in allit]
    if not new: break
    for i in new: allit[i['gid']]=i
    end=min(i['date'] for i in new)
print('total after paging',len(allit)); ds=sorted(i['date'] for i in allit.values()); print('range',f(ds[0]),f(ds[-1]))
c=collections.Counter(str(f(i['date']))[:4] for i in allit.values()); print(sorted(c.items()))
upd=[i for i in allit.values() if 'update' in i['title'].lower() and i['feedname']=='steam_community_announcements']
print('Update-titled community announcements',len(upd))
for k in ['Trade','Operation','Major','Case','Capsule','Souvenir']:
    m=sorted((f(i['date']),i['title']) for i in allit.values() if k.lower() in i['title'].lower())
    print(k,len(m),m[:2],m[-2:])
json.dump(list(allit.values()),open('/private/tmp/claude-501/-Users-daniilmurai--claude-spares-spare-EGwwSp/82e44451-4f47-4269-a3f4-2c29f8a2e988/scratchpad/news.json','w'))
