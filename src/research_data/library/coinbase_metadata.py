"""Read explicit primary-page publisher fields, never CMS sys timestamps."""
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit
from .time import timestamp

VERSION='coinbase-primary-v1'

class PublisherState(HTMLParser):
 def __init__(self):super().__init__();self.active=False;self.parts=[]
 def handle_starttag(self,tag,attrs):
  attrs=dict(attrs)
  if tag=='script' and attrs.get('id')=='server-app-state' and attrs.get('type')=='application/json':self.active=True
 def handle_data(self,data):
  if self.active:self.parts.append(data)
 def handle_endtag(self,tag):
  if tag=='script':self.active=False

def primary_metadata(raw,url):
 parsed=urlsplit(url)
 if parsed.hostname not in ('www.coinbase.com','coinbase.com') or not parsed.path.startswith('/institutional/research-insights/'):return {}
 p=PublisherState();p.feed(raw.decode('utf-8',errors='replace'))
 if not p.parts:return {'version':VERSION,'status':'no_publisher_state'}
 try:state=json.loads(''.join(p.parts))
 except ValueError:return {'version':VERSION,'status':'invalid_publisher_state'}
 slug=parsed.path.rstrip('/').rsplit('/',1)[-1];matches=[]
 def unpack(value):
  for _ in range(3):
   if not isinstance(value,str) or value.lstrip()[:1] not in ('{','['):return value
   try:value=json.loads(value)
   except ValueError:return None
  return value
 bridges=unpack(state.get('suspenseBridgeData',[]))
 if not isinstance(bridges,list) or len(bridges)>1000:return {'version':VERSION,'status':'invalid_primary_page_bridges'}
 for bridge in bridges:
  node=unpack(bridge)
  for key in ('data','page','fields'):node=unpack(node.get(key)) if isinstance(node,dict) else None
  if isinstance(node,dict) and node.get('slug')==slug and 'publicationDate' in node and isinstance(node.get('content'),dict):matches.append(node)
 if not matches:return {'version':VERSION,'status':'no_matching_primary_publication_date'}
 try:dates={timestamp(row['publicationDate']).isoformat() for row in matches}
 except (ValueError,TypeError):return {'version':VERSION,'status':'invalid_primary_publication_date'}
 if len(dates)!=1:return {'version':VERSION,'status':'conflicting_primary_publication_dates'}
 page=matches[0];info=page.get('content',{}).get('fields',{}).get('pageInformation',{}).get('fields',{});download=info.get('download',{}).get('fields',{})
 result={'version':VERSION,'status':'matched_primary_page','slug':slug,'title':page.get('title'),'published_at':next(iter(dates)),
         'publisher_date_raw':page['publicationDate'],'publication_time_source':'server-app-state/suspenseBridgeData/data/page/fields/publicationDate'}
 if isinstance(download.get('bynderUrl'),str):
  result['download_url']=download['bynderUrl'];result['download_is_public_direct']=download.get('easyDownload') is True
 return result
