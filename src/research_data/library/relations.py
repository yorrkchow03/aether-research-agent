"""Deterministic version and identical-text links; no inferred copy direction."""
import hashlib,json
from collections import defaultdict
from pathlib import Path
from research_data.http import dumps

VERSION='document-links-v1'
PREDICATES=('content_revision_of','metadata_revision_of','identical_normalized_text')

def normalized_text(text):return ' '.join(text.split())

def link_documents(library):
 with library.connect(readonly=True) as c:
  rows=[{**json.loads(r['body']),'known_at':r['known_at']} for r in c.execute('SELECT body,known_at FROM document_versions ORDER BY known_at,rowid')]
 versions=defaultdict(list);groups=defaultdict(list);links=[]
 for row in rows:
  versions[row['document_id']].append(row)
  text=normalized_text(row['text'])
  if len(text)>=500:groups[hashlib.sha256(text.encode()).hexdigest()].append(row)
 for history in versions.values():
  for before,after in zip(history,history[1:]):
   changed=any(before.get(k)!=after.get(k) for k in ('title','text','published_at'))
   links.append((after['record_id'],'content_revision_of' if changed else 'metadata_revision_of',before['record_id'],dumps({'algorithm':VERSION,'scope':'Normalized record version comparison; this does not establish a publisher correction or a new event'},sort_keys=True)))
 for sha,group in groups.items():
  documents={}
  for row in group:documents.setdefault(row['document_id'],row)
  matches=sorted(documents.values(),key=lambda r:r['record_id'])
  if len(matches)>1:
   for row in matches[1:]:links.append((row['record_id'],'identical_normalized_text',matches[0]['record_id'],dumps({'algorithm':VERSION,'normalized_text_sha256':sha,'scope':'Exact whitespace-normalized body match. Sources remain distinct; origin, copy direction and event equivalence are not inferred.'},sort_keys=True)))
 with library.connect() as c:
  for predicate in PREDICATES:c.execute('DELETE FROM relations WHERE predicate=?',(predicate,))
  c.executemany('INSERT INTO relations VALUES(?,?,?,?)',links)
 code=library.object(Path(__file__).read_bytes())
 counts={p:sum(r[1]==p for r in links) for p in PREDICATES}
 library.job('document-relationships','collected',algorithm=VERSION,implementation_sha256=code,document_versions=len(rows),relations=counts)
 return counts
