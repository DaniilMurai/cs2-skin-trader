import json,collections
d=json.load(open('out/all.json'))
s=[v for k,v in d.items() if k.startswith('skin-') and not k.startswith('skin-vanilla')]
print('skin keys',sorted(s[0].keys()))
print('with crates',sum(1 for v in s if v.get('crates')),'with collections',sum(1 for v in s if v.get('collections')),'of',len(s))
print('crate keys',sorted(d['crate-4017'].keys()) if 'crate-4017' in d else '')
cr=[v for k,v in d.items() if k.startswith('crate-')]
print('crates w/ first_sale_date',sum(1 for v in cr if v.get('first_sale_date')),'of',len(cr))
cs=[v for k,v in d.items() if k.startswith('collection')]
print('collections w/ release_date',sum(1 for v in cs if v.get('release_date')),'of',len(cs))
print('sticker keys with date?',[k for k in d['sticker-1'] if 'date' in k], 'agent/patch/music date keys',[k for t in ('agent-4613','patch-4550','music_kit-1','graffiti-1653') for k in d[t] if 'date' in k])

inc=set(); incc=set()
for k,v in d.items():
    if k.startswith('crate-'):
        for c in v.get('contains',[])+v.get('contains_rare',[]): inc.add(c['id'])
    if k.startswith('collection'):
        for c in v.get('contains',[]): incc.add(c['id'])
sids={v['skin_id'] for v in s}
print('distinct skins',len(sids),'in some crate.contains',len(sids&inc),'in some collection.contains',len(sids&incc),'either',len(sids&(inc|incc)))
cr_dates=[(v['name'],v['first_sale_date']) for v in cr if v.get('first_sale_date')]
print(cr_dates[:3]); print({k:v for k,v in d['crate-4017'].items() if k in('first_sale_date','type','name')})
print('release_date sample',[(v['name'],v['release_date']) for v in cs if v.get('release_date')][:3])
