"""Read-only feasibility check of an MLS-addressable public broker source."""
import json
import re
import hashlib
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

agent='PA-Property-PhotoReader/1.0'
origin='https://www.thepreferredrealty.com'
results=[]
try:
    with urlopen(Request(origin+'/robots.txt',headers={'User-Agent':agent}),timeout=20) as response:
        text=response.read(250001).decode('utf8')
        if response.status!=200 or len(text)>250000 or 'user-agent:' not in text.lower() or '<html' in text.lower():
            raise ValueError('robots_unavailable')
    rules=RobotFileParser();rules.parse(text.splitlines())
    for mls in ('1779745','1779799'):
        url=origin+'/real-estate/'+mls+'/'
        if not rules.can_fetch(agent,url):
            results.append({'listing_id':mls,'status':'robots_disallowed'});break
        time.sleep(10)
        try:
            with urlopen(Request(url,headers={'User-Agent':agent,'Accept':'text/html'}),timeout=20) as response:
                body=response.read(2000001)
                result={'listing_id':mls,'http_status':response.status,'content_type':response.headers.get('Content-Type'),'bytes':len(body)}
                results.append(result)
                if response.status!=200:break
                if len(body)<=2000000:
                    Path('provider-'+mls+'.html').write_bytes(body)
                    urls=list(dict.fromkeys(re.findall(r'<img[^>]+src="(https://api\.cotality\.com/[^\"]+)"[^>]+alt="[^\"]+ - photo [123]"',body.decode('utf8'))))[:3]
                    result['images']=[]
                    for image_url in urls:
                        time.sleep(10)
                        try:
                            with urlopen(Request(image_url,headers={'User-Agent':agent,'Accept':'image/jpeg'}),timeout=20) as image:
                                image_body=image.read(3000001)
                                result['images'].append({'url':image_url,'status':image.status,'mime':image.headers.get('Content-Type'),'bytes':len(image_body),'jpeg':image_body.startswith(b'\xff\xd8\xff'),'sha256':hashlib.sha256(image_body).hexdigest()})
                        except HTTPError as error:
                            result['images'].append({'status':error.code});break
        except HTTPError as error:
            results.append({'listing_id':mls,'status':'source_unavailable','http_status':error.code});break
except (HTTPError,URLError,ValueError,OSError) as error:
    results.append({'status':'robots_unavailable','http_status':getattr(error,'code',None),'error':str(error)[:120]})
print(json.dumps({'results':results}))
