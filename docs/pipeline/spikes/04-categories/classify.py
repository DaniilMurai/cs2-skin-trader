import json,re,collections,sys
d=json.load(open('out/all.json'))
prices=[x['market_hash_name'] for x in json.load(open('out/prices.json'))['items']]
idx={}
for k,v in d.items():
    t=k.split('-')[0] if not k.startswith('skin-vanilla') else 'skin'
    if k.startswith('sticker_slab'): t='sticker_slab'
    if k.startswith('collection'): continue
    m=v.get('market_hash_name')
    if m: idx.setdefault(m,(t,v))
# also derive name-based index for records without market_hash_name (stickers, music kit, collectibles)
for k,v in d.items():
    t=k.split('-')[0]
    if t in('sticker','music_kit','collectible','pet','tool','key') and not v.get('market_hash_name'):
        idx.setdefault(v['name'],(t,v))
        if t=='music_kit': idx.setdefault('Music Kit | '+v['name'],(t,v))
wear=re.compile(r' \((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$')
def classify(n):
    base=wear.sub('',n)
    for cand in (n,base,re.sub(r'^(StatTrak™ |Souvenir )','',base),re.sub(r'^★ (StatTrak™ )?','★ ',base)):
        if cand in idx:
            t,v=idx[cand]; return t,'db',v
    return None,None,None
def rules(n):
    b=re.sub(r'^(StatTrak™ |Souvenir )','',n)
    if b.startswith('★'): return 'glove' if re.search(r'Gloves|Wraps',b) else 'knife'
    if n.startswith('Sticker |'): return 'sticker'
    if n.startswith('Sealed Graffiti |') or n.startswith('Graffiti |'): return 'graffiti'
    if n.startswith('Patch |'): return 'patch'
    if n.startswith('Charm |') or n.startswith('Souvenir Charm |'): return 'charm'
    if n.startswith('Music Kit |') or 'Music Kit' in n: return 'music_kit'
    if re.search(r'Sticker Capsule|Autograph Capsule|Patch Capsule|Pins Capsule|Souvenir Package|Graffiti Box',n): return 'capsule_or_package'
    if re.search(r'\bCase\b|Weapon Case',n): return 'case'
    if re.search(r' \| ',n) and wear.search(n): return 'weapon_skin'
    if re.search(r'Key$|Pass$|Tag$|Tool|Pin$| Coin$|Viewer',n): return 'other'
    return None
cat=lambda t,v,n:{'skin':None,'sticker':'sticker','sticker_slab':'sticker','graffiti':'graffiti','patch':'patch','keychain':'charm','agent':'agent','music_kit':'music_kit','collectible':'other','pet':'other','tool':'other','key':'other'}.get(t,t)
out=collections.Counter(); src=collections.Counter(); unk=[]; sub=collections.Counter(); rar=0; skins=0; wt=collections.Counter()
res={}
for n in prices:
    t,s,v=classify(n)
    if t=='skin':
        c=v['category']['name'] if v.get('category') else '?'
        c='knife' if c=='Knives' else 'glove' if c=='Gloves' else 'weapon_skin'
        skins+=c=='weapon_skin'
        if c=='weapon_skin':
            rar+=bool(v.get('rarity')); wt[v['category']['name']]+=1
        src[s]+=1
    elif t=='crate':
        ct=v.get('type')
        c='case' if ct=='Case' else 'souvenir_package' if ct=='Souvenir' else 'capsule' if ct and 'Capsule' in ct else 'souvenir_package' if False else 'capsule' if ct in('Pins','Souvenir Highlight','Graffiti') else 'music_kit_box' if ct=='Music Kit Box' else 'other_crate:'+str(ct)
        src[s]+=1
    elif t:
        c=cat(t,v,n); src[s]+=1
    else:
        c=rules(n); src['rules' if c else 'unknown']+=1
        if c is None: c='UNKNOWN'; unk.append(n)
    out[c]+=1; res[n]=c
N=len(prices)
for c,k in out.most_common(): print(f'{c:22}{k:7}{100*k/N:7.2f}%')
print('src',src,'N',N)
print('weapon skins w/ rarity',rar,'/',skins); print(wt)
print('UNKNOWN',len(unk),100*len(unk)/N)
import random; random.seed(1)
for u in random.sample(unk,min(20,len(unk))): print('  ',u)
json.dump(res,open('out/categories.json','w'))
