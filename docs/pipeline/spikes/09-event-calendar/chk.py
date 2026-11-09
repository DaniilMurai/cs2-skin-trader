exec(open('droppool_news.py').read().split("print('official posts'")[0])
import datetime
for i in sorted(allit.values(),key=lambda i:i['date']):
    if f(i['date']) in (datetime.date(2025,12,18),datetime.date(2026,3,11)):
        c=i['contents']; j=c.find('CARE PACKAGE'); print(f(i['date']),i['title'],'|',re.sub(r'\s+',' ',c[j-20:j+500]) if j>=0 else 'no care package section')
