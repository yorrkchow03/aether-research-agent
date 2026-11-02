"""Low-cost publisher feed checks; feed excerpts never masquerade as fetched articles."""
import hashlib
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from .public_web import fetch_page,PageText
from .knowledge import canonical_url
from research_data.http import utcnow
from research_data.normalize import iso,timestamp

ETH_FEED='https://blog.ethereum.org/feed.xml'


def scan_feed(store,url=ETH_FEED):
    fetched=fetch_page(url);root=ET.fromstring(fetched['raw']);now=utcnow()
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
        rid='web-'+hashlib.sha256((link+digest+now).encode()).hexdigest()[:24]
        row={'record_id':rid,'dataset_id':'online.publisher.feed','source_kind':'feed_item','title':title,'url':link,
             'text':text,'published_at':date,'observed_at':date or now,'retrieved_at':now,'available_at':now,
             'values':{},'units':{},'quality_flags':['publisher_feed_excerpt','article_not_independently_fetched','publication_time_unverified'],
             'source':{'source_id':rid,'name':'Ethereum Foundation blog feed','url':url,'sha256':digest,'retrieved_at':now},
             'provider_annotation':{'article_url':link,'publication_metadata':published},'raw_path':str(raw)}
        store.register(row);ids.append(rid)
    return {'status':'ok','items':len(ids),'retrieved_at':now,'source_url':url}
