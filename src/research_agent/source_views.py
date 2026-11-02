"""Small, exact navigation views over a saved original; never new evidence.

The caller still resolves the original evidence ID and validates its provenance.
This module does not fetch, write, paraphrase, or decide whether a source is true.
Offsets count Python Unicode characters in the complete saved text.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from .evidence_memory import exact_excerpts


SEPARATOR = '\n[…]\n'
METADATA = ('record_id', 'dataset_id', 'title', 'url', 'published_at', 'retrieved_at',
    'available_at', 'first_seen_at', 'observed_at', 'source_kind', 'asset', 'assets',
    'scope', 'quality_flags')
SOURCE_METADATA = ('source_id', 'name', 'url', 'sha256', 'retrieved_at', 'published_at', 'format')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def focused_source_view(row, query, max_chars=3200):
    """Return exact keyword-selected slices with original ID, clocks and hashes.

``text`` is the model-visible joined view, not a replacement for the normalized
record. ``source_slices`` bind each part back to the complete original; the
separator is explicitly not publisher text. An omitted passage can contain a
material qualification, so a view is a navigation aid and not proof of absence.
The original remains readable with the same evidence ID and an empty query.
    """
    if not isinstance(row, dict) or not isinstance(row.get('record_id'), str) or not row['record_id']:
        raise ValueError('Supply a saved evidence record with its original ID')
    if not isinstance(query, str) or len(query)>8000:
        raise ValueError('Supply a bounded research query')
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 400<=max_chars<=12000:
        raise ValueError('Focused source budget must be 400..12000 characters')
    text=row.get('text')
    text_field='text'
    if text is None:
        text=row.get('excerpt','');text_field='excerpt'
    if not isinstance(text,str):
        raise ValueError('Saved source text must be a string')
    # The reused selector supplies exact text, without an LLM or semantic call.
    parts=deepcopy(exact_excerpts(text,query,maximum=max_chars))
    # Separators are navigation text too: a multi-span view must not exceed its
    # declared text allowance merely because joining adds extra characters.
    excess=sum(part['end']-part['start'] for part in parts)+max(0,len(parts)-1)*len(SEPARATOR)-max_chars
    while excess>0 and parts:
        part=parts[-1];remove=min(excess,part['end']-part['start'])
        part['end']-=remove;part['text']=text[part['start']:part['end']]
        excess-=remove
        if part['start']==part['end']:
            parts.pop();excess-=len(SEPARATOR) if parts else 0
    spans=[];previous=0;omitted=[]
    for part in parts:
        start,end=part['start'],part['end']
        if not 0<=start<end<=len(text) or start<previous or part['text']!=text[start:end]:
            raise ValueError('Selector returned a non-exact or overlapping source slice')
        if start>previous:omitted.append({'start':previous,'end':start})
        spans.append({'start':start,'end':end,'sha256':_sha(part['text'])})
        previous=end
    if previous<len(text):omitted.append({'start':previous,'end':len(text)})
    joined=SEPARATOR.join(part['text'] for part in parts)
    view={key:deepcopy(row[key]) for key in METADATA if key in row}
    source=row.get('source')
    if isinstance(source,dict):view['source']={key:deepcopy(source[key]) for key in SOURCE_METADATA if key in source}
    pointer={'record_id':row['record_id'],'read_evidence_arguments':{'record_id':row['record_id'],'query':''}}
    if isinstance(row.get('data_file'),str):
        pointer['data_file']=row['data_file']
        view['data_file']=row['data_file']
        if row['data_file'].startswith('/data/'):
            terminal='./'+row['data_file'].lstrip('/')
            pointer['terminal_data_file']=terminal;view['terminal_data_file']=terminal
            view['path_scope']='data_file is virtual for file tools; terminal_data_file is relative to the session terminal workspace'
    if isinstance(row.get('terminal_data_file'),str):pointer['terminal_data_file']=row['terminal_data_file']
    view.update(view_kind='focused_saved_source',query=query,text=joined,
        source_slices=spans,source_text_field=text_field,offset_unit='unicode_characters',
        separator=SEPARATOR,separator_is_source_text=False,
        full_text_sha256=_sha(text),full_text_characters=len(text),
        view_text_sha256=_sha(joined),selected_source_characters=sum(s['end']-s['start'] for s in spans),
        source_text_truncated=bool(omitted),omitted_ranges=omitted,original_access=pointer,
        source_scope='Exact slices of this saved text representation, not a new evidence record or a paraphrase. '
            'Source kind still distinguishes original pages from search/feed snippets. '
            'Omitted text can contain conditions, counterevidence or corrections. Read the original '
            'or request another focused query before making an absence or completeness claim.')
    return view
