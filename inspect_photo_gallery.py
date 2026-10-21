"""Bounded read-only inspection of observed public listing templates."""
import json
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import listing_photo_fallback as photo

cases = [('1774389','e0c453d1115683e0cc91470cba6e7fb6'),
         ('1774350','c845a999fdab0cc24417112295a0b8ab'),
         ('1773867','e2d01801e96a6005e36408e5aa483fc8'),
         ('1774318','2adc5b14f37f0980dfc37555494f664e')]
reader=photo.Reader(max_requests=5,seconds=120)
out=Path('photo-gallery-inspection');out.mkdir(exist_ok=True)
results=[]
for mls,key in cases:
    receipt=json.loads((photo.ROOT/(key+'.json')).read_text())
    street,city,state,zip_code=receipt['identity']
    row={'id':receipt['property_id'],'docket_id':'MLS-'+mls,'source_type':'mls',
         'address':street,'city':city,'state':state,'zip':zip_code,'county':receipt['county'],
         'url':receipt['inventory_source_url']}
    item={'listing_id':mls,'expected':receipt['identity']}
    try:
        url=photo.discover(reader,row,'preferredrealty')
        html=reader.page(url)
        (out/('listing-'+mls+'.html')).write_text(html,encoding='utf8')
        soup=BeautifulSoup(html,'html.parser')
        item['title']=soup.title.get_text(' ',strip=True) if soup.title else ''
        item['images']=[]
        for img in soup.find_all('img'):
            if urlparse(img.get('src','')).hostname!='api.cotality.com':continue
            item['images'].append({'alt':img.get('alt'),'ancestors':[
                {'tag':p.name,'class':p.get('class')} for p in list(img.parents)[:5]]})
        try:item['parsed_photos']=len(photo.parse_listing(html,row,url,'preferredrealty'))
        except photo.Unavailable as error:item['parser_status']=error.status
    except photo.Unavailable as error:
        item.update(status=error.status,http_status=error.http_status)
    results.append(item)
report={'results':results,'network_requests':reader.requests}
(out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print(json.dumps(report))
