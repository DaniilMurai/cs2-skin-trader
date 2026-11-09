import json,subprocess,base64,re,datetime
raw=subprocess.run(['gh','api','repos/azzaazeel/Next-js-Blog-Boilerplate-master/contents/data-raw/CS2%20Container%20Drop%20Pool.md','-q','.content'],capture_output=True,text=True).stdout
t=base64.b64decode(raw).decode()
def pd(s):
    s=s.replace('June','Jun').replace('July','Jul'); return datetime.datetime.strptime(s.strip(),'%Y, %d %b').date()
ext={}
for l in t.splitlines():
    p=l.split('\t')
    if len(p)>=2 and re.match(r'\d{4}, ',p[1]):
        try: ext[p[0]]=pd(p[1])
        except Exception as e: pass
print('cases in ConteX table',len(ext))
d=json.load(open('/Users/daniilmurai/PycharmProjects/cs2-skin-trader/data/cache/items_all.json'))
by={v['name']:v['first_sale_date'] for k,v in d.items() if k.startswith('crate-') and v.get('first_sale_date')}
diffs=[]
for n,dt in ext.items():
    b=by.get(n)
    if b:
        b=datetime.date.fromisoformat(b.replace('/','-')) if re.match(r'\d{4}-\d\d-\d\d',b.replace('/','-')) and len(b.replace('/','-').split('-')[1])==2 and len(b.replace('/','-').split('-')[2])==2 else datetime.datetime.strptime(b,'%Y/%m/%d').date()
        diffs.append(((b-dt).days,n,str(b),str(dt)))
print('matched',len(diffs))
import statistics
print('ByMykel minus ConteX, days: median',statistics.median(x[0] for x in diffs),'min',min(diffs)[:2],'max',max(diffs)[:2])
print('|diff|<=3d:',sum(abs(x[0])<=3 for x in diffs),'of',len(diffs))
for x in sorted(diffs)[:5]+sorted(diffs)[-3:]: print(x)
