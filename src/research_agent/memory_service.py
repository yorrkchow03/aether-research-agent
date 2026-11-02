"""Derived event/source indexing; original knowledge/library remains canonical."""
from __future__ import annotations
import re
from .event_memory_index import EventMemoryIndex
from .hybrid_memory import HybridMemoryRetriever


def index_source(index,row,*,max_cards=24):
    if not row.get('text') or not str(row.get('record_id','')).startswith(('page-','web-')):
        return {'indexed':False,'reason':'not_public_text'}
    saved=index.upsert_source(row);text=row['text'];cards=[]
    # Exact paragraph/chunk pointers, not extracted semantic facts or generated summaries.
    for match in re.finditer(r'\S[^\n]*(?:\n(?!\n)[^\n]*)*',text):
        start,end=match.span()
        for offset in range(start,end,1400):
            if len(cards)>=max_cards:break
            cards.append(index.record_card(row['record_id'],start=offset,end=min(offset+1400,end),entity_keys=row.get('assets') or ([row['asset']] if row.get('asset') else [])))
        if len(cards)>=max_cards:break
    return {**saved,'indexed':True,'cards':len(cards),'source_chars':len(text),
        'card_coverage':'bounded_exact_paragraphs','card_limit_reached':len(cards)>=max_cards,
        'scope':'Derived navigation; read original evidence before asserting a fact.'}


def open_memory_service(root,state_dir,knowledge,encoder_path):
    index=EventMemoryIndex(state_dir/'event-memory',source_lookup=knowledge.get,
        source_lookup_many=knowledge.get_many)
    encoder=None;issue=None
    try:
        from .local_memory_encoder import LocalMemoryEncoder
        encoder=LocalMemoryEncoder(root/encoder_path)
    except (ValueError,FileNotFoundError,ImportError,OSError) as exc:issue=type(exc).__name__
    retriever=HybridMemoryRetriever(index,encoder=encoder,encoder_id=encoder.model_id if encoder else None,
        cache_fingerprint=(encoder.fingerprint+':'+encoder.preprocessing) if encoder else None,
        max_document_chars=100000)
    return index,retriever,{'encoder':'local_pretrained_static' if encoder else 'unavailable',
        'model_id':encoder.model_id if encoder else None,'issue':issue,
        'scope':'Independent hybrid candidates over indexed subset; full existing lexical source recall remains available.'}
