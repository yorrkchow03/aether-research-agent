"""Bounded, resumable fixed-source discovery and original-text acquisition."""
from __future__ import annotations
import json,re
from datetime import datetime,timezone
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit,urlencode

from research_data.normalize import timestamp,iso
from research_agent.public_web import PageText
from .http import PublicCapture
from .registry import TEXT_SOURCES,install
from .store import WINDOW_START,WINDOW_END
from .time import timestamp

RELEVANT=re.compile(r'ethereum|\bether\b|\beth\b|bitcoin|\bbtc\b|crypto|blockchain|stablecoin|\bdefi\b|\bstaking\b|tokeniz|federal funds|\bfomc\b|federal open market committee|monetary policy|interest rate',re.I)
ETH_MENTION=re.compile(r'ethereum|\bether\b|\beth\b',re.I)
SELECTION_VERSION='publisher-asset-keywords-v2'
DATE_PATH=re.compile(r'/(20\d\d)[/-](\d{1,2})[/-](\d{1,2})(?:/|\b)')
COINDESK_ARTICLE_PATH=re.compile(r'^/[a-z][a-z0-9-]*/20\d\d/')

def path_date(url):
 match=DATE_PATH.search(url)
 return f'{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}' if match else None

def empty_forum_post_state(post):
 # Discourse Post.types small_action=3. Status/visibility actions have no
 # article body by design; do not invent text or treat them as lost articles.
 if post.get('post_type')==3 and post.get('action_code'):
  return 'excluded','publisher_nontext_system_action'
 return 'body_unavailable','Forum post missing cooked text'

class Links(HTMLParser):
 def __init__(self):super().__init__();self.links=[];self.current=None;self.label=[]
 def handle_starttag(self,tag,attrs):
  if tag=='a':self.current=dict(attrs).get('href');self.label=[]
 def handle_data(self,data):
  if self.current:self.label.append(data)
 def handle_endtag(self,tag):
  if tag=='a' and self.current:self.links.append((self.current,' '.join(self.label)));self.current=None

class ArticleHTML(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=False);self.parts=[];self.depth=0;self.best=[];self.priority=0
 def handle_starttag(self,tag,attrs):
  rank=3 if dict(attrs).get('id')=='article' else 2 if tag=='article' else 1 if tag=='main' else 0
  if not self.depth and rank>self.priority:self.parts=[];self.depth=1;self.priority=rank
  elif self.depth and tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):self.depth+=1
  if self.depth:self.parts.append(self.get_starttag_text())
 def handle_endtag(self,tag):
  if self.depth:
   self.parts.append('</'+tag+'>');self.depth-=1
   if not self.depth:self.best=list(self.parts)
 def handle_data(self,data):
  if self.depth:self.parts.append(data)
 def handle_entityref(self,name):self.handle_data('&'+name+';')
 def handle_charref(self,name):self.handle_data('&#'+name+';')

class SemanticHTML(HTMLParser):
 """V4 scope extraction; preserve V3 replay and handle void/self-closing tags."""
 VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
 def __init__(self,main=True):super().__init__(convert_charrefs=False);self.main=main;self.stack=[];self.parts=[];self.chunks=[]
 def handle_starttag(self,tag,attrs):
  attrs=dict(attrs);match=(tag=='main' or attrs.get('role')=='main' or attrs.get('id')=='main') if self.main else (tag=='article' or attrs.get('id')=='article')
  if not self.stack:
   if not match or tag in self.VOID:return
   self.parts=[]
  self.parts.append(self.get_starttag_text())
  if tag not in self.VOID:self.stack.append(tag)
 def handle_startendtag(self,tag,attrs):
  self.handle_starttag(tag,attrs)
  if tag not in self.VOID:self.handle_endtag(tag)
 def handle_endtag(self,tag):
  if self.stack and tag in self.stack:
   self.parts.append('</'+tag+'>');idx=len(self.stack)-1-self.stack[::-1].index(tag);self.stack=self.stack[:idx]
   if not self.stack:self.chunks.append(''.join(self.parts))
 def handle_data(self,data):
  if self.stack:self.parts.append(data)
 def handle_entityref(self,name):self.handle_data('&'+name+';')
 def handle_charref(self,name):self.handle_data('&#'+name+';')

def html_text(raw,*,body_only=False,scope_version=3,source_url=None):
 p=PageText();p.feed(raw.decode('utf-8',errors='replace'))
 text=p.text();meta=dict(p.publication_metadata)
 # Publisher-visible date line, not an arbitrary date mentioned in prose or
 # the year of a report's subject. Ambiguous multiple dates remain unknown.
 if not meta.get('published_at'):
  dates=[]
  for line in text.splitlines():
   line=line.strip()
   if re.fullmatch(r'(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2},? 20\d\d',line):
    try:dates.append(datetime.strptime(line.replace(',',''),'%B %d %Y').replace(tzinfo=timezone.utc).isoformat())
    except ValueError:pass
  if len(set(dates))==1:meta.update(published_at=dates[0],publication_time_source='publisher_standalone_visible_date_line_unverified')
 if body_only:
  if scope_version==4:
   for main in (True,False):
    extract=SemanticHTML(main);extract.feed(raw.decode('utf-8',errors='replace'))
    if extract.chunks:
     body=PageText();body.feed(max(extract.chunks,key=len));text=body.text();meta['body_extraction']='publisher_semantic_main' if main else 'publisher_article_container_v4';break
   else:meta['body_extraction']='whole_page_fallback'
  else:
   extract=ArticleHTML();extract.feed(raw.decode('utf-8',errors='replace'))
   if extract.best:
    body=PageText();body.feed(''.join(extract.best));text=body.text();meta['body_extraction']='publisher_article_or_main_container'
   else:meta['body_extraction']='whole_page_fallback'
 if scope_version==4 and source_url:
  from .coinbase_metadata import primary_metadata
  publisher=primary_metadata(raw,source_url)
  if publisher:
   meta['publisher_primary']=publisher;meta['publisher_metadata_version']=publisher['version']
   if publisher.get('published_at'):meta.update(published_at=publisher['published_at'],publication_time_source=publisher['publication_time_source'])
 return p.title(),text,meta

class TextCollector:
 def __init__(self,library,*,refresh=False):
  self.library=library;self.http=PublicCapture(library,refresh=refresh);self.start=library.start;self.end=library.end;install(library)

 def run(self,sources=None):
  sources=sources or list(TEXT_SOURCES)
  prior_reuse=self.http.reuse_connections;self.http.reuse_connections=True
  try:
   with ThreadPoolExecutor(max_workers=3) as pool:
    futures={pool.submit(self.source,name):name for name in sources}
    for future in as_completed(futures):
     name=futures[future]
     try:future.result()
     except Exception as exc:
      self.library.job('text:'+name,'failed',error=type(exc).__name__+': '+str(exc)[:1000],discovery_complete=False)
      print('TEXT GAP '+name+' '+str(exc)[:180],flush=True)
  finally:
   self.http.close();self.http.reuse_connections=prior_reuse
  reconcile_discovery(self.library)
  from .relations import link_documents
  link_documents(self.library)

 def source(self,name):
  if name not in TEXT_SOURCES:raise ValueError('Unknown fixed source')
  base,strategy,url=TEXT_SOURCES[name]
  self.library.job('text:'+name,'running',entry_url=url)
  if strategy=='discourse':result=self.discourse(name,base)
  elif name=='decrypt':result=self.decrypt_archive(base)
  elif strategy=='sitemap':result=self.sitemap(name,base,url)
  else:result=self.index(name,base,url)
  self.library.job('text:'+name,'collected',**result)
  print('TEXT COLLECTED '+name+' '+str(result),flush=True)

 def article(self,name,url,published=None,title_hint=''):
  if published is not None:published=timestamp(published).isoformat()
  scoped=name=='coinbase_institutional' or (name=='federal_reserve' and '/fomcminutes' in url)
  extraction='PageText-v5-publisher' if name=='coinbase_institutional' else 'PageText-v4-semantic' if scoped else 'PageText-v3-utc'
  with self.library.connect(readonly=True) as c:
   old=c.execute('SELECT status FROM discoveries WHERE source_id=? AND url=?',(name,url)).fetchone()
  if old and old[0]=='saved' and not self.http.refresh:
   with self.library.connect(readonly=True) as c:
    current=c.execute('SELECT v.body FROM document_versions v JOIN documents d USING(document_id) WHERE d.source_id=? AND d.url=? ORDER BY v.rowid DESC LIMIT 1',(name,url)).fetchone()
   if current:
    metadata=json.loads(current[0]).get('library_metadata',{})
    if metadata.get('extraction')==extraction and metadata.get('selection_version')==SELECTION_VERSION and (name!='coinbase_institutional' or metadata.get('publication_metadata',{}).get('publisher_primary',{}).get('status')=='matched_primary_page'):
     if name=='coinbase_institutional':
      from .pdf_reports import collect_report
      collect_report(self.library,json.loads(current[0]),self.http)
     return
  reason=nonarticle_reason(name,url)
  if reason:
   self.library.discover(name,url,status='excluded',reason=reason);return
  self.library.discover(name,url,published_at=published,title_hint=title_hint)
  if name=='coindesk':
   self.library.discover(name,url,published_at=published,status='access_policy_review',reason='Publisher robots publishes specific AI-crawler restrictions. Metadata retained; obtain licensed/authorized article access before bulk text ingestion.')
   return
  try:
   cid,raw=self.http.fetch(name,url)
   title,text,meta=html_text(raw,body_only=True,scope_version=4 if scoped else 3,source_url=url)
   if meta.get('publisher_primary',{}).get('status') in ('conflicting_primary_publication_dates','invalid_primary_publication_date'):
    self.library.discover(name,url,status='date_unresolved',reason='Publisher primary publication field is invalid/conflicting',capture_id=cid);return
   date=meta.get('published_at') or published
   if not date:
    date=path_date(url)
   if not date:
    self.library.discover(name,url,status='date_unresolved',reason='No trustworthy publication date in publisher metadata or dated URL');return
   date=timestamp(date)
   if not self.start<=date<self.end:
    self.library.discover(name,url,published_at=date,status='excluded',reason='outside_window',capture_id=cid);return
   relevance_text=text if name in ('sec','federal_reserve') else text[:3000]
   if name not in ('ethereum','bitcoin_core','aave','lido','magicians','coinbase_institutional') and not RELEVANT.search(title+' '+relevance_text):
    self.library.discover(name,url,published_at=date,status='excluded',reason='No configured asset/macro topic match',capture_id=cid);return
   if len(text)<200 or re.search(r'just a moment|access denied|enable javascript to continue',text[:500],re.I):
    self.library.discover(name,url,published_at=date,status='body_unavailable',reason='Short, challenge or nonarticle content');return
   if re.search(r'subscribe to (?:continue|read)|sign in to (?:continue|read)|this (?:article|content) is (?:only|exclusively)',text,re.I):
    self.library.discover(name,url,published_at=date,status='restricted',reason='Publisher subscriber restriction; no excerpt labelled as full text');return
   assets=['ETH'] if name in ('ethereum','aave','lido','magicians') else ['BTC'] if name=='bitcoin_core' else []
   if ETH_MENTION.search(title+' '+text) and 'ETH' not in assets:assets.append('ETH')
   if re.search(r'bitcoin|\bbtc\b',title+' '+text,re.I) and 'BTC' not in assets:assets.append('BTC')
   if not assets:assets=['ETH','BTC']
   rid=self.library.put_document(name,url,title or title_hint,text,cid,published_at=date,
     kind='external_report' if name=='coinbase_institutional' else 'official_release' if name in ('ethereum','bitcoin_core','sec','federal_reserve') else 'article',
     assets=assets,metadata={'publication_metadata':meta,'extraction':extraction,'selection_version':SELECTION_VERSION,'relevance_scope':'Publisher article body for regulators/central bank; title/introduction for news','timestamp_parser':'Explicit UTC for publisher date-only/naive timestamps','characters':len(text),'source_date_hint':published,
       'document_role':'report_landing_page' if meta.get('publisher_primary',{}).get('download_url') else 'publisher_article_text','visual_content_scope':'Text extracted; referenced images/charts are not numeric observations'})
   if name=='coinbase_institutional':
    from .pdf_reports import collect_report
    collect_report(self.library,self.library.evidence(rid),self.http)
  except Exception as exc:
   self.library.discover(name,url,published_at=published,status='failed',reason=type(exc).__name__+': '+str(exc)[:700])

 def sitemap(self,name,base,url):
  queue=[url];seen=set();candidates={};errors=[]
  while queue:
   current=queue.pop(0)
   if current in seen:continue
   if len(seen)>=120:
    errors.append('Sitemap safety ceiling reached; discovery incomplete');break
   seen.add(current)
   try:
    cid,raw=self.http.fetch(name,current);tree=ET.fromstring(raw)
    if name=='decrypt' and current!=url and tree.tag.endswith('urlset') and not len(tree):
     errors.append(current+': publisher declared recent article shard is empty; no full coverage inferred')
   except Exception as exc:
    if len(seen)==1:
     # Use only publisher-declared sitemap locations, not guessed third-party mirrors.
     try:
      _,robots=self.http.fetch(name,base+'/robots.txt')
      declared=re.findall(r'^Sitemap:\s*(https://\S+)',robots.decode(errors='replace'),re.M|re.I)
      declared=[u for u in declared if urlsplit(u).hostname==urlsplit(base).hostname and u not in seen]
      if declared:queue.extend(declared);continue
     except Exception:pass
    errors.append(current+': '+str(exc)[:200]);continue
   for node in tree:
    location=node.findtext('{*}loc');modified=node.findtext('{*}lastmod')
    if not location:continue
    path=urlsplit(location).path
    if name=='ethereum' and not tree.tag.endswith('sitemapindex') and not path_date(location):continue
    if name=='decrypt':
     if tree.tag.endswith('sitemapindex') and not re.fullmatch(r'/post-sitemap\d*\.xml',path):continue
     if not tree.tag.endswith('sitemapindex') and not re.match(r'^/\d+/',path):continue
    if name=='coindesk':
     if tree.tag.endswith('sitemapindex') and not re.fullmatch(r'/sitemaps/articles-\d+\.xml',path):continue
     if not tree.tag.endswith('sitemapindex') and not COINDESK_ARTICLE_PATH.match(path):continue
    if name=='coinbase_institutional' and not tree.tag.endswith('sitemapindex') and not path.startswith('/institutional/research-insights/'):continue
    if tree.tag.endswith('sitemapindex'):
     if modified:
      try:
       if timestamp(modified)<self.start:continue
      except ValueError:pass
     queue.append(location);continue
    date=node.findtext('.//{*}publication_date') or path_date(location)
    if date and not self.start<=timestamp(date)<self.end:continue
    if not date and modified:
     try:
      if timestamp(modified)<self.start:continue
     except ValueError:pass
    if name=='ethereum' and re.search(r'/(?:zh|ja|ko|es|fr|de|pt|tr|vi|it|ar|ru)/',urlsplit(location).path):continue
    candidates[location]=date
   self.library.job('text:'+name,'running',sitemaps_checked=len(seen),discovered=len(candidates),pending_sitemaps=len(queue))
  for i,(article,date) in enumerate(candidates.items()):
   self.article(name,article,date)
   if i%20==0:self.library.job('text:'+name,'running',sitemaps_checked=len(seen),discovered=len(candidates),articles_processed=i+1)
  if name=='decrypt' and errors:
   # The RSS mixes current articles with old videos. Its oldest timestamp
   # cannot certify monthly article coverage or pagination termination.
   from email.utils import parsedate_to_datetime
   cid,raw=self.http.fetch(name,base+'/feed');feed=ET.fromstring(raw)
   for item in feed.findall('./channel/item'):
    link=item.findtext('link') or '';date=parsedate_to_datetime(item.findtext('pubDate'))
    if self.start<=date<self.end and not nonarticle_reason(name,link):self.article(name,link,date,item.findtext('title') or '')
  return {'discovery_complete':not errors,'discovery_errors':errors,'sitemaps_checked':len(seen),'candidate_urls':len(candidates),'selection_rule':'Dated URL in window, or recently modified/undated URL followed by publisher publication-date check'}

 def discourse(self,name,base):
  topics={};page=0;exhausted=False;listing_errors=[]
  while page<200:
   url=base+'/latest.json?'+urlencode({'page':page})
   cid,obj=self.http.json(name,url);items=obj['topic_list'].get('topics',[])
   if not items:exhausted=True;break
   recent=[]
   for topic in items:
    at=topic.get('last_posted_at') or topic.get('created_at')
    if at and timestamp(at)>=self.start:topics[topic['id']]=topic;recent.append(topic)
   self.library.job('text:'+name,'running',listing_page=page,topics=len(topics))
   if not recent and not obj['topic_list'].get('more_topics_url'):exhausted=True;break
   nonpinned=[t for t in items if not t.get('pinned')]
   if nonpinned and all(timestamp(t.get('last_posted_at') or t['created_at'])<self.start for t in nonpinned):exhausted=True;break
   page+=1
  for ti,topic in enumerate(topics.values()):
   topic_url=base+'/t/'+str(topic['id'])+'.json'
   try:
    cid,obj=self.http.json(name,topic_url)
    posts=obj.get('post_stream',{}).get('posts',[]);stream=obj.get('post_stream',{}).get('stream',[]);have={p['id'] for p in posts}
    missing=[pid for pid in stream if pid not in have]
    if len(missing)>5000:
     listing_errors.append({'topic':topic['id'],'reason':'Long-thread safety bound; no complete coverage claimed'});continue
    packets=[(cid,posts)]
    for i in range(0,len(missing),20):
     query=urlencode({'post_ids[]':missing[i:i+20]},doseq=True)
     pcid,payload=self.http.json(name,base+'/t/'+str(topic['id'])+'/posts.json?'+query)
     packets.append((pcid,payload.get('post_stream',{}).get('posts',payload.get('posts',[]))))
    for pcid,post_rows in packets:
     for post in post_rows:
      published=post.get('created_at')
      if not published or not self.start<=timestamp(published)<self.end:continue
      url=base+'/t/'+obj.get('slug',topic.get('slug','topic'))+'/'+str(topic['id'])+'/'+str(post['post_number'])
      parser=PageText();parser.feed(post.get('cooked',''));text=parser.text()
      if not text:
       status,reason=empty_forum_post_state(post)
       self.library.discover(name,url,published_at=published,status=status,reason=reason,capture_id=pcid,post_id=post['id'],post_type=post.get('post_type'),action_code=post.get('action_code'),availability_state='legitimate_no_text_observation' if status=='excluded' else 'unresolved_missing');continue
      self.library.put_document(name,url,obj.get('title',topic['title'])+' — post '+str(post['post_number']),text,pcid,
          published_at=published,kind='forum_post',assets=['ETH'],metadata={'topic_id':topic['id'],'post_id':post['id'],'post_number':post['post_number'],'publisher_updated_at':post.get('updated_at'),'content_version':'Current publisher API capture; edits not backdated','container_url':topic_url})
   except Exception as exc:
    listing_errors.append({'topic':topic['id'],'reason':str(exc)[:300]})
    self.library.discover(name,topic_url,status='failed',reason=str(exc)[:500])
   self.library.job('text:'+name,'running',listing_pages=page+1,topics=len(topics),topics_processed=ti+1,errors=listing_errors)
  return {'discovery_complete':exhausted and not listing_errors,'listing_pages':page+1,'topics':len(topics),'errors':listing_errors,'selection_rule':'Topics with activity since start; original posts filtered by creation time, current edits separately labelled'}

 def index(self,name,base,url):
  if name=='sec':return self.sec_rss(name,base)
  urls=[url]
  if name=='federal_reserve':urls=[base+'/newsevents/pressreleases/2026-press.htm',base+'/newsevents/speech/2026-speeches.htm',base+'/newsevents/testimony/2026-testimony.htm']
  candidates={};listing_errors=[]
  for index_url in urls:
   try:
    _,raw=self.http.fetch(name,index_url);p=Links();p.feed(raw.decode(errors='replace'))
    for href,label in p.links:
     absolute=urljoin(base,href);path=urlsplit(absolute).path
     if urlsplit(absolute).hostname!=urlsplit(base).hostname:continue
     date=None;match=DATE_PATH.search(path)
     if match:date='-'.join(match.groups())
     compact=re.search(r'(2026)(\d\d)(\d\d)',path)
     if compact and not date:date='-'.join(compact.groups())
     if date and not self.start<=timestamp(date)<self.end:continue
     patterns={'bitcoin_core':r'/en/2026/', 'federal_reserve':r'/newsevents/(?:pressreleases|speech|testimony)/\w+2026\d{4}',
               'sec':r'/newsroom/press-releases/', 'coinbase_institutional':r'/institutional/research-insights/(?:research|reports|market-intelligence)/'}
     if re.search(patterns[name],path) and absolute!=index_url:candidates[absolute]=(date,label)
   except Exception as exc:listing_errors.append(str(exc))
  for article,(date,label) in candidates.items():self.article(name,article,date,label)
  # These publishers may paginate or render only recent subsets. Do not certify that a landing page is a full archive.
  full_listing=name in ('bitcoin_core','federal_reserve') and not listing_errors
  return {'discovery_complete':full_listing,'candidate_urls':len(candidates),'listing_errors':listing_errors,'listing_urls':urls,
          'remaining_discovery':'Verify full monthly index/pagination' if not full_listing else None,'selection_rule':'Publisher listing, dated URLs and original publication metadata'}

 def sec_rss(self,name,base):
  from email.utils import parsedate_to_datetime
  cid,raw=self.http.fetch(name,'https://www.sec.gov/news/pressreleases.rss')
  tree=ET.fromstring(raw);items=tree.findall('./channel/item');dates=[];count=0
  for node in items:
   published=parsedate_to_datetime(node.findtext('pubDate'));dates.append(published)
   if self.start<=published<self.end:
    self.article(name,node.findtext('link'),iso(published),node.findtext('title') or 'SEC release');count+=1
   else:self.library.discover(name,node.findtext('link'),published_at=published,status='excluded',reason='outside_window_from_official_RSS')
  result={'discovery_complete':bool(dates) and min(dates)<self.start,'feed_items':len(items),'candidate_urls':count,
          'oldest_listing_date':iso(min(dates)) if dates else None,'selection_rule':'Source RSS spans before the fixed start; within-window releases checked individually'}
  if not result['discovery_complete']:
   result['archive_fallback']=self.sec_archive(name,base)
   result['discovery_complete']=result['archive_fallback']['discovery_complete']
  return result

 def sec_archive(self,name,base):
  # Follow the official descending dated table's declared next-page links.
  # A short RSS feed alone cannot establish six-month discovery completeness.
  url=base+'/newsroom/press-releases';visited=set();candidate=set();pages=0;complete=False;errors=[];oldest=None
  while url and pages<40:
   if url in visited:errors.append('Official SEC archive repeated a page');break
   visited.add(url);pages+=1
   try:
    cid,raw=self.http.fetch(name,url);html=raw.decode(errors='replace');rows=[]
    for chunk in re.findall(r'<tr\b[^>]*>.*?</tr>',html,re.S|re.I):
     date=re.search(r'<time\b[^>]*datetime=["\']([^"\']+)',chunk,re.I)
     if not date:continue
     links=Links();links.feed(chunk)
     for href,label in links.links:
      target=urljoin(base,href)
      if urlsplit(target).hostname==urlsplit(base).hostname and '/newsroom/press-releases/' in urlsplit(target).path:rows.append((target,timestamp(date[1]),label))
    if not rows:raise ValueError('Official SEC archive has no dated release rows; inspect parser/source')
    dates=[row[1] for row in rows]
    if any(b>a for a,b in zip(dates,dates[1:])):raise ValueError('Official SEC date order is not descending')
    oldest=min(dates)
    for target,date,label in rows:
     if self.start<=date<self.end:
      candidate.add(target);self.article(name,target,date,label)
    if oldest<self.start:complete=True;break
    links=Links();links.feed(html)
    following=[urljoin(url,href) for href,label in links.links if 'Next' in label and 'page=' in href]
    if not following:complete=True;break
    url=following[0]
    self.library.job('text:'+name,'running',listing_pages=pages,archive_candidates=len(candidate),oldest_listing_date=iso(oldest),discovery_complete=False)
   except Exception as exc:errors.append(type(exc).__name__+': '+str(exc)[:400]);break
  if not complete and not errors:errors.append('SEC archive safety bound reached')
  return {'discovery_complete':complete and not errors,'listing_pages':pages,'candidate_urls':len(candidate),'oldest_listing_date':iso(oldest) if oldest else None,'errors':errors,'listing_urls':sorted(visited),'selection_rule':'Official descending dated press-release archive, follow declared Next link until before configured start or exhausted'}


 def decrypt_archive(self,base):
  # Endpoint and pagination schema were obtained from the publisher's own
  # public Next.js client. No login, private key or third-party mirror.
  candidates={};seen=set();complete=False;pages=0;errors=[]
  for page in range(1,101):
   query='{articles(filters:{locale:{eq:"en"},category:{slug:{eq:"news"}}},pagination:{page:'+str(page)+',pageSize:100},sort:["publishedAt:desc"]){data{id slug title publishedAt} pagination{page pageCount pageSize total}}}'
   cid,obj=self.http.json('decrypt','https://gateway.decrypt.co/?'+urlencode({'query':query}));packet=obj.get('data',{}).get('articles')
   if not packet:raise ValueError('Publisher archive GraphQL errors: '+str(obj.get('errors'))[:500])
   items=packet['data'];ids=tuple(r['id'] for r in items)
   if ids in seen:raise ValueError('Publisher archive repeated a page')
   seen.add(ids);pages=page
   if packet['pagination']['page']!=page:raise ValueError('Publisher archive ignored requested page')
   dates=[timestamp(r['publishedAt']) for r in items]
   if any(b>a for a,b in zip(dates,dates[1:])):raise ValueError('Archive order is not descending')
   for row in items:
    date=timestamp(row['publishedAt']);url=base+'/'+str(row['id'])+'/'+row['slug']
    if self.start<=date<self.end:
     candidates[url]=row;self.library.discover('decrypt',url,published_at=date,title_hint=row['title'],listing_capture_id=cid)
   if not items or min(dates)<self.start or page>=packet['pagination']['pageCount']:complete=True;break
   self.library.job('text:decrypt','running',listing_page=page,discovered=len(candidates),discovery_complete=False)
  if not complete:errors.append('Archive page safety limit reached')
  for i,(url,row) in enumerate(candidates.items()):
   self.article('decrypt',url,timestamp(row['publishedAt']),row['title'])
   if i%20==0:self.library.job('text:decrypt','running',listing_pages=pages,discovered=len(candidates),articles_processed=i+1,discovery_complete=complete)
  return {'discovery_complete':complete,'discovery_errors':errors,'listing_pages':pages,'candidate_urls':len(candidates),'selection_rule':'Publisher public news archive, descending original publication time, paginated past fixed start; original page relevance and body checks','archive_endpoint':'https://gateway.decrypt.co/','earlier_sitemap_failure':'Publisher recent article sitemap shards were empty; retained captures document why the public archive replaced sitemap discovery'}


def nonarticle_reason(name,url):
 path=urlsplit(url).path
 if name=='coinbase_institutional' and path.lower().endswith('.pdf') and (urlsplit(url).hostname or '').endswith(('.ctfassets.net','.bynder.com')):return None
 if name=='ethereum' and not path_date(url):return 'navigation_or_category_page'
 if name=='bitcoin_core' and not path_date(url):return 'release_index_not_an_article'
 if name=='coindesk' and not COINDESK_ARTICLE_PATH.match(path):return 'nonarticle_or_video_outside_fixed_text_scope'
 if name=='decrypt' and not re.match(r'^/\d+/',path):return 'navigation_or_collection_not_an_article'
 if name=='coinbase_institutional' and ('/media' in path or path.rstrip('/').count('/')<5):return 'media_or_navigation_not_a_text_report'
 return None


def reconcile_discovery(library):
 # Keep rejected discovery rows and all original capture receipts for audit.
 with library.connect(readonly=True) as c:rows=list(c.execute('SELECT source_id,url,published_at,status,reason,body FROM discoveries'))
 for row in rows:
  reason=nonarticle_reason(row['source_id'],row['url'])
  if not reason and row['status']=='excluded':reason='selection:'+str(row['reason']).removeprefix('selection:')
  if reason:
   library.discover(row['source_id'],row['url'],published_at=row['published_at'],status='excluded',reason=reason,**json.loads(row['body']))
   with library.connect() as c:
    for doc in c.execute('SELECT document_id FROM documents WHERE source_id=? AND url=?',(row['source_id'],row['url'])):
     metadata=json.loads(row['body']);capture=metadata.get('capture_id')
     if capture:
      effective=library.capture_info(capture)['retrieved_at']
     else:
      effective=c.execute('SELECT min(known_at) FROM document_versions WHERE document_id=?',(doc[0],)).fetchone()[0]
     c.execute('INSERT OR REPLACE INTO relations VALUES(?,?,?,?)',(doc[0],'excluded_from_research',reason,json.dumps({'reason':reason,'effective_known_at':effective,'source_capture_id':capture})))
  elif row['status']=='saved':
   with library.connect() as c:
    c.execute("DELETE FROM relations WHERE predicate='excluded_from_research' AND subject IN (SELECT document_id FROM documents WHERE source_id=? AND url=?)",(row['source_id'],row['url']))
