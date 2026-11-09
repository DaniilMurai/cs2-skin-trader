import json,collections
d=json.load(open('data/cache/items_all.json'))
cr=[v for k,v in d.items() if k.startswith('crate-')]
print('crates',len(cr),'with first_sale_date',sum(1 for c in cr if c.get('first_sale_date')))
print('types',collections.Counter(c.get('type') for c in cr))
yrs=collections.Counter((c.get('first_sale_date') or 'none')[:4] for c in cr); print(sorted(yrs.items()))
for t in set(c.get('type') for c in cr):
    sub=[c for c in cr if c.get('type')==t and c.get('first_sale_date')]
    ds=sorted(c['first_sale_date'] for c in sub)
    print(t,len(sub),ds[:1],ds[-1:])
# names containing Capsule / Souvenir / Case
for kw in ['Capsule','Souvenir','Case','Package']:
    s=[c for c in cr if kw in c['name']]; print(kw,len(s),sum(1 for c in s if c.get('first_sale_date')))
print([ (c['name'],c['first_sale_date']) for c in cr if 'Capsule' in c['name']][:8])
print([ (c['name'],c['first_sale_date']) for c in cr if 'Souvenir' in c['name']][:6])
cs=[v for k,v in d.items() if k.startswith('collection-')]
print('collections',len(cs),'with release_date',sum(1 for c in cs if c.get('release_date')))
print(sorted((c['release_date'],c['name']) for c in cs if c.get('release_date'))[-5:])
print('last cases',sorted((c['first_sale_date'],c['name']) for c in cr if c.get('first_sale_date') and c['name'].endswith('Case'))[-6:])
print('any drop-pool keys', [k for k in cr[0]], [k for k in cs[0]])
