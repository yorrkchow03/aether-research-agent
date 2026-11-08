"""Small, exact navigation views over a saved original; never new evidence.

The caller still resolves the original evidence ID and validates its provenance.
This module does not fetch, write, paraphrase, or decide whether a source is true.
Offsets count Python Unicode characters in the complete saved text.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re

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
    return _source_view(row,text,text_field,parts,query=query,view_kind='focused_saved_source')


def _source_view(row,text,text_field,parts,*,query='',view_kind):
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
    pointer={'record_id':row['record_id'],'read_evidence_arguments':{'record_id':row['record_id'],'query':'','full_text':True}}
    if isinstance(row.get('data_file'),str):
        pointer['data_file']=row['data_file']
        view['data_file']=row['data_file']
        if row['data_file'].startswith('/data/'):
            terminal='./'+row['data_file'].lstrip('/')
            pointer['terminal_data_file']=terminal;view['terminal_data_file']=terminal
            view['path_scope']='data_file is virtual for file tools; terminal_data_file is relative to the session terminal workspace'
    if isinstance(row.get('terminal_data_file'),str):pointer['terminal_data_file']=row['terminal_data_file']
    view.update(view_kind=view_kind,query=query,text=joined,
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


class _ArticleContainers(HTMLParser):
    """Collect closed generic publisher containers; never execute page content."""
    VOID=frozenset({'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'})
    BODY_CLASSES=frozenset({'entry-content','article-content','article-body','post-content','story-body','story-content'})

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack=[];self.active=[];self.candidates=[];self.suppressed=0;self.limited=False

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript','svg'):
            self.suppressed+=1
        if self.suppressed:return
        tokens=set((attrs.get('class') or '').split())
        rank=(4 if attrs.get('itemprop')=='articleBody' or tokens & self.BODY_CLASSES
              else 3 if tag=='article' else 2 if tag=='main' or attrs.get('role')=='main' or attrs.get('id')=='main' else 0)
        # Forum posts are an exchange, not separate articles. Exclude the tempting
        # first-post itemprop=text container; semantic main keeps later replies.
        marker='article_body_container' if rank==4 else 'article_container' if rank==3 else 'main_container'
        if rank and tag not in self.VOID:
            if len(self.active)>=16 or len(self.candidates)>=128:self.limited=True
            else:self.active.append({'depth':len(self.stack),'tag':tag,'rank':rank,'method':marker,'parts':[]})
        for candidate in self.active:candidate['parts'].append(self.get_starttag_text())
        if tag not in self.VOID:self.stack.append(tag)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg'):
            if self.suppressed:self.suppressed-=1
            return
        if self.suppressed:return
        if tag not in self.stack:return
        index=len(self.stack)-1-self.stack[::-1].index(tag)
        for candidate in self.active:candidate['parts'].append('</'+tag+'>')
        # Only a matching, fully closed container is usable. Implicitly closed
        # malformed inner scopes are discarded rather than swallowing the page.
        remaining=[]
        for candidate in self.active:
            if candidate['depth']==index and candidate['tag']==tag:
                self.candidates.append(candidate)
            elif candidate['depth']<index:
                remaining.append(candidate)
        self.active=remaining;self.stack=self.stack[:index]

    def handle_data(self,data):
        if not self.suppressed:
            for candidate in self.active:candidate['parts'].append(data)

    def handle_entityref(self,name):self.handle_data('&'+name+';')
    def handle_charref(self,name):self.handle_data('&#'+name+';')


class _Headlines(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.parts=[];self.headings=[]
    def handle_starttag(self,tag,attrs):
        if tag=='h1':self.active=True;self.parts=[]
    def handle_endtag(self,tag):
        if tag=='h1' and self.active:
            self.headings.append(' '.join(''.join(self.parts).split()));self.active=False
    def handle_data(self,data):
        if self.active:self.parts.append(data)


def _headline_matches(title,headings):
    norm=lambda s:' '.join(re.findall(r'\w+',s.casefold()))
    title=norm(title)
    return bool(title and any(len(norm(h))>=12 and (norm(h) in title or title in norm(h)) for h in headings))


def _raw_article_candidates(row,raw_reader=None):
    """Only hash-verified saved HTML may suggest publisher body boundaries."""
    raw=raw_reader(row) if raw_reader else None
    if raw is None:
        with Path(row['raw_path']).open('rb') as stream:raw=stream.read(2_000_001)
    if not isinstance(raw,bytes) or len(raw)>2_000_000:raise ValueError('Saved HTML exceeds the bounded navigation reader')
    expected=(row.get('source') or {}).get('sha256')
    if not isinstance(expected,str) or hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('Saved original response hash mismatch')
    content_type=row.get('content_type') or (row.get('source') or {}).get('format') or ''
    if content_type and 'html' not in content_type.lower():return []
    decoded=raw.decode('utf-8',errors='replace')
    if not re.search(r'<(?:html|article|main|div|section)\b',decoded,re.I):return []
    parser=_ArticleContainers();parser.feed(decoded);parser.close()
    if parser.limited:raise ValueError('Semantic container scan bound reached; no body boundary inferred')
    forum=(row.get('source_kind') in ('forum_post','forum_topic','governance_topic') or
           'DiscussionForumPosting' in decoded or len(re.findall(r'data-post-id\s*=',decoded,re.I))>1)
    from .public_web import PageText
    rows=[]
    for candidate in parser.candidates:
        fragment=''.join(candidate['parts'])
        extract=PageText();extract.feed(fragment);extract.close()
        headings=_Headlines();headings.feed(fragment);headings.close()
        body=extract.text()
        # Tiny article cards are navigation, and cannot establish a document body.
        if len(body)>=120:rows.append({'text':body,'rank':candidate['rank'],'method':candidate['method'],
            'headings':headings.headings,'forum_exchange':forum})
    return rows


def _bounded_body_parts(text,start,end,maximum):
    if end-start<=maximum:return [{'start':start,'end':end,'text':text[start:end]}] if end>start else []
    # Preserve both the opening and final qualifications; no claim is made about
    # the omitted middle. Focused reads or explicit full access remain available.
    available=maximum-len(SEPARATOR);head=available*2//3;tail=available-head
    return [{'start':start,'end':start+head,'text':text[start:start+head]},
            {'start':end-tail,'end':end,'text':text[end-tail:end]}]


def article_source_view(row,max_chars=6000,*,raw_reader=None):
    """Navigate to a hash-verified semantic body using exact canonical offsets.

    This never rewrites the saved source or labels a publisher container pure
    article prose. A body must match the canonical text exactly and uniquely.
    Ambiguous/malformed/unavailable HTML falls back to a declared whole-source
    window. ``max_chars`` bounds visible source text, not serialized metadata.
    """
    # Reuse the existing public input and metadata contract, including private
    # path exclusion, without using its keyword slices as body evidence.
    baseline=focused_source_view(row,'',max_chars=max_chars)
    text=row.get('text') if row.get('text') is not None else row.get('excerpt','')
    text_field=baseline['source_text_field']
    detection={'status':'fallback','method':'whole_source_window','reason':'no_unique_verified_semantic_container'}
    start,end=0,len(text)
    if text_field=='text' and text:
        try:
            candidates=_raw_article_candidates(row,raw_reader)
            matched=[]
            for candidate in candidates:
                offset=text.find(candidate['text'])
                if offset<0:continue
                if text.find(candidate['text'],offset+1)>=0:continue
                matched.append({**candidate,'start':offset,'end':offset+len(candidate['text'])})
            if matched and any(candidate['forum_exchange'] for candidate in matched):
                # Multiple forum contributions are one exchange. A single post
                # is not a safe substitute for a captured topic with replies.
                matched=[candidate for candidate in matched if candidate['method']=='main_container']
                if not matched:detection['reason']='forum_scope_needs_complete_saved_exchange'
            if matched:
                priority=max(candidate['rank'] for candidate in matched)
                unique={(c['start'],c['end']):c for c in matched if c['rank']==priority}
                ordered=sorted(unique.values(),key=lambda c:len(c['text']),reverse=True)
                matching=[c for c in ordered if _headline_matches(row.get('title') or '',c['headings'])]
                if matching:ordered=matching
                elif any(c['headings'] for c in ordered) and row.get('title'):
                    ordered=[];detection['reason']='publisher_headline_does_not_match_saved_title'
                if ordered:
                    best=ordered[0]
                    # Nested identical bodies and trivial recommendation cards do
                    # not create false ambiguity. Comparable unrelated containers do.
                    others=[c for c in ordered[1:] if not (best['start']<=c['start'] and c['end']<=best['end'])]
                    if not others or len(best['text'])>=max(400,2*len(others[0]['text'])):
                        start,end=best['start'],best['end']
                        detection={'status':'mapped','method':best['method'],
                            'reason':'unique_exact_match_to_saved_canonical_text','start':start,'end':end,
                            'characters':end-start,'candidate_count':len(unique)}
                    else:detection['reason']='ambiguous_comparable_semantic_containers'
        except (OSError,KeyError,ValueError,TypeError) as exc:
            detection['reason']='saved_html_unavailable_or_unverified'
            detection['error_type']=type(exc).__name__
    parts=_bounded_body_parts(text,start,end,max_chars)
    view=_source_view(row,text,text_field,parts,view_kind='article_saved_source')
    view.pop('query',None)
    view['body_detection']=detection
    view['body_text_truncated']=end-start>sum(part['end']-part['start'] for part in parts)
    view['original_access']['read_evidence_arguments']['full_text']=True
    view['source_scope']=('Exact source navigation, not a new source or factual summary. A mapped publisher '
        'container can include unrelated material; an unmatched view is a whole-source window. '
        'Omitted text can contain corrections or counterevidence. Use a focused query or explicit '
        'full_text read/data file before claiming absence or complete article coverage.')
    return view
