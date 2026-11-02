"""Original public report bytes and page-labelled digital text, never OCR claims."""
import hashlib,json
from io import BytesIO
from urllib.parse import urlsplit
from pypdf import PdfReader
from .store import instant

VERSION='pypdf-text-v1'

def extract_pdf(raw):
 if not raw.startswith(b'%PDF-'):raise ValueError('Publisher attachment is not an original PDF')
 reader=PdfReader(BytesIO(raw))
 if reader.is_encrypted:raise ValueError('Encrypted report needs independent authorized access')
 if not 1<=len(reader.pages)<=500:raise ValueError('PDF page count exceeds the bounded text extractor')
 pages=[page.extract_text() or '' for page in reader.pages]
 if sum(len(p.strip()) for p in pages)<200:raise ValueError('No sufficient digital text layer; do not label a scan as extracted full text')
 text='\n\n'.join('[Page '+str(i+1)+']\n'+page.strip() for i,page in enumerate(pages))
 return text,{'page_count':len(pages),'nonempty_text_pages':sum(bool(p.strip()) for p in pages),
              'text_layer_only':True,'ocr_performed':False,'raw_original_complete':True,
              'extraction_scope':'Digital PDF text with page boundaries. Original images/charts remain in the raw PDF; their content and numeric interpretation are not certified by text extraction.'}

def collect_report(library,parent,http):
 primary=parent.get('library_metadata',{}).get('publication_metadata',{}).get('publisher_primary',{})
 url=primary.get('download_url')
 if not url:return {'status':'no_primary_attachment'}
 if not primary.get('download_is_public_direct'):return {'status':'publisher_gate_not_accessed'}
 host=urlsplit(url).hostname or ''
 if urlsplit(url).scheme!='https' or not (host.endswith('.ctfassets.net') or host.endswith('.bynder.com') or host=='static-assets.coinbase.com'):raise ValueError('Primary report host needs explicit publisher-source review')
 job='attachment:'+hashlib.sha256(url.encode()).hexdigest()[:20]
 library.job(job,'running',source_url=url)
 try:
  cap,raw=http.fetch('coinbase_institutional',url);text,meta=extract_pdf(raw)
  meta.update(extraction=VERSION,parent_page_evidence_id=parent['record_id'],parent_page_url=parent['url'],
              publisher_date_source='Linked primary publisher page publicationDate, not PDF creation date',
              original_pdf_sha256=hashlib.sha256(raw).hexdigest(),format='PDF')
  rid=library.put_document('coinbase_institutional',url,parent['title']+' [PDF]',text,cap,published_at=parent['published_at'],kind='external_report_pdf',assets=parent.get('assets'),metadata=meta)
  with library.connect() as c:c.execute('INSERT OR IGNORE INTO relations VALUES(?,?,?,?)',(rid,'report_attachment_of',parent['record_id'],json.dumps({'scope':'Publisher primary direct-download relation; publication claim comes from parent, not file creation metadata'})))
  library.discover('coinbase_institutional',url,published_at=parent['published_at'],status='saved',record_id=rid,parent_page_evidence_id=parent['record_id'],material_type='external_report_pdf',text_layer_only=True)
  library.job(job,'collected',source_url=url,record_id=rid,source_range_complete=True)
  return {'status':'saved','record_id':rid}
 except Exception as exc:
  library.discover('coinbase_institutional',url,published_at=parent.get('published_at'),status='body_unavailable',reason=type(exc).__name__+': '+str(exc)[:500],material_type='external_report_pdf',parent_page_evidence_id=parent['record_id'])
  library.job(job,'failed',source_url=url,error=type(exc).__name__+': '+str(exc)[:500]);return {'status':'body_unavailable'}
