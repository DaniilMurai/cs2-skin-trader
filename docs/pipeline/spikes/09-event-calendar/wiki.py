import json,re,urllib.request,urllib.parse,gzip,time
UA="cs2-skin-trader-research/0.1 (personal research; github.com/daniilmurai)"
def get(u,**p):
    rq=urllib.request.Request(u+'?'+urllib.parse.urlencode(p),headers={'User-Agent':UA,'Accept-Encoding':'gzip'})
    r=urllib.request.urlopen(rq,timeout=60); b=r.read()
    if r.headers.get('Content-Encoding')=='gzip': b=gzip.decompress(b)
    print('HTTP',r.status,len(b)); return json.loads(b)
# Wikipedia: wikitext of the Majors page
j=get("https://en.wikipedia.org/w/api.php",action='parse',page='Counter-Strike_Major_Championships',prop='wikitext',format='json')
w=j['parse']['wikitext']['*']; print(len(w))
open('/private/tmp/claude-501/-Users-daniilmurai--claude-spares-spare-EGwwSp/82e44451-4f47-4269-a3f4-2c29f8a2e988/scratchpad/wp.wiki','w').write(w)
i=w.find('{| class="wikitable'); print(w[i:i+1500])
# Liquipedia: tournament pages via action=query (infobox has sdate/edate); 3 pages, 2 s apart
for t in ['BLAST/Major/2025/Austin','PGL/2024/Copenhagen','DreamHack/2013/Winter']:
    q=get("https://liquipedia.net/counterstrike/api.php",action='query',titles=t,prop='revisions',rvprop='content',rvslots='main',format='json',formatversion=2)
    c=q['query']['pages'][0]['revisions'][0]['slots']['main']['content']
    print(t,re.findall(r'\|(sdate|edate|date)=([^\n|]*)',c)[:3],re.findall(r'\|publishertier=([^\n|]*)',c))
    time.sleep(2.5)
