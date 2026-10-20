"""Read-only feasibility check of an MLS-addressable public broker source."""
import json
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
                if len(body)<=2000000:Path('provider-'+mls+'.html').write_bytes(body)
        except HTTPError as error:
            results.append({'listing_id':mls,'status':'source_unavailable','http_status':error.code});break
except (HTTPError,URLError,ValueError,OSError) as error:
    results.append({'status':'robots_unavailable','http_status':getattr(error,'code',None),'error':str(error)[:120]})
print(json.dumps({'results':results}))
