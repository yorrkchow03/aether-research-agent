"""Low-cost publisher feed checks; feed excerpts never masquerade as fetched articles."""
import hashlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from .public_web import fetch_page,PageText
from .knowledge import canonical_url
from research_data.http import utcnow
from research_data.normalize import iso,timestamp

ETH_FEED='https://blog.ethereum.org/feed.xml'

PUBLISHERS = (
    ('Ethereum Foundation', ETH_FEED, 'ETH', None),
    ('Aave Governance', 'https://governance.aave.com/latest.rss', 'ETH', None),
    ('Lido Research', 'https://research.lido.fi/latest.rss', 'ETH', None),
    ('Ethereum Magicians', 'https://ethereum-magicians.org/latest.rss', 'ETH', None),
    ('Federal Reserve', 'https://www.federalreserve.gov/feeds/press_all.xml', None,
     re.compile(r'FOMC|Federal Open Market Committee|federal funds|monetary policy|stablecoin|digital asset', re.I)),
    ('SEC', 'https://www.sec.gov/news/pressreleases.rss', None,
     re.compile(r'Ethereum|Ether\b|stablecoin|digital asset|crypto|tokeniz', re.I)),
)


def scan_feed(store,url=ETH_FEED,*,publisher='Ethereum Foundation',asset=None,since=None,match=None,timeout=None):
    fetched=fetch_page(url) if timeout is None else fetch_page(url, timeout=timeout)
    root=ET.fromstring(fetched['raw']);now=utcnow()
    if root.tag.split('}')[-1] not in ('rss','feed','RDF'):
        raise ValueError('Publisher did not return an RSS or Atom document')
    digest=hashlib.sha256(fetched['raw']).hexdigest()
    raw=store.root/'feed-cache'/(digest+'.xml');raw.parent.mkdir(exist_ok=True);raw.write_bytes(fetched['raw'])
    entries=root.findall('./channel/item') or root.findall('{*}entry');ids=[]
    for item in entries[:40]:
        title=item.findtext('title') or item.findtext('{*}title') or 'Publisher update'
        link=item.findtext('link')
        if not link:
            node=item.find('{*}link');link=node.get('href') if node is not None else None
        if not link:continue
        try:link=canonical_url(link)
        except ValueError:continue
        content=item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or item.findtext('description') or item.findtext('{*}summary') or title
        parser=PageText();parser.feed(content);text=parser.text()[:6000]
        published=item.findtext('pubDate') or item.findtext('{*}published');date=None
        if published:
            try:date=iso(timestamp(published))
            except (ValueError,TypeError):
                try:date=iso(parsedate_to_datetime(published))
                except (ValueError,TypeError):pass
        if since and (not date or timestamp(date)<timestamp(since)):continue
        if match and not match.search(title+' '+text):continue
        rid='web-'+hashlib.sha256((link+digest+now).encode()).hexdigest()[:24]
        row={'record_id':rid,'dataset_id':'online.publisher.feed','source_kind':'feed_item','title':title,'url':link,
             'text':text,'published_at':date,'observed_at':date or now,'retrieved_at':now,'available_at':now,
             'values':{},'units':{},'quality_flags':['publisher_feed_excerpt','article_not_independently_fetched','publication_time_unverified'],
             'source':{'source_id':rid,'name':publisher+' feed','url':url,'sha256':digest,'retrieved_at':now},
             'provider_annotation':{'article_url':link,'publication_metadata':published},'raw_path':str(raw)}
        if asset:row['asset']=asset
        ids.append(store.observe_feed_item(row))
    return {'status':'ok','items':len(ids),'record_ids':ids,'retrieved_at':now,'source_url':url}
