import json,re,urllib.request,urllib.parse,gzip
UA="cs2-skin-trader-research/0.1 (personal research; github.com/daniilmurai)"
def get(u,**p):
    rq=urllib.request.Request(u+'?'+urllib.parse.urlencode(p),headers={'User-Agent':UA,'Accept-Encoding':'gzip'})
    r=urllib.request.urlopen(rq,timeout=60); b=r.read()
    if r.headers.get('Content-Encoding')=='gzip': b=gzip.decompress(b)
    print('HTTP',r.status,len(b)); return json.loads(b)
# 1 action=parse request only (ToS: parse <= 1 per 30 s)
j=get("https://liquipedia.net/counterstrike/api.php",action='parse',page='Majors',prop='wikitext',format='json')
w=j['parse']['wikitext']['*']
open('/private/tmp/claude-501/-Users-daniilmurai--claude-spares-spare-EGwwSp/82e44451-4f47-4269-a3f4-2c29f8a2e988/scratchpad/majors.wiki','w').write(w)
print(len(w)); i=w.find('Dreamhack'); print(w[:100]); 
rows=re.findall(r'\|\s*(\d{4}-\d{2}-\d{2})[^\n]*',w); print(len(rows),rows[:5])
for m in re.finditer(r'\n\|[^\n]*(Major|Championship)[^\n]*',w[:20000]): 
    print(m.group(0)[:160]); 
