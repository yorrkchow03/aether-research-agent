"""Initial depth hints and adaptive research guidance.

The recognizer is a display/prompt hint, not a capability or delegation gate.
The agent chooses the needed depth from the actual question and evidence gaps.
"""
import re


def requests_research(prompt):
    text=re.sub(r'```[\s\S]*?```|`[^`]*`|“[^”]*”|「[^」]*」|"[^"\n]*"', ' ', prompt).strip()
    if re.search(r'(?:不要|不用|无需|别)\s*(?:再)?\s*(?:深入|详细|全面|系统)?\s*(?:研究|调查|分析)|\b(?:do not|don\x27t|no need to)\s+(?:research|investigate|analy[sz]e)\b',text,re.I):
        return False
    return bool(re.search(
        r'(?:^|[，,。；;]\s*)'
        r'(?:(?:请(?:你)?|帮我|给我|我想让你|麻烦(?:你)?)\s*)*'
        r'(?:(?:深入|详细|全面|系统|多来源|多角度)\s*(?:研究|调查|分析)|(?:研究|调查)(?:一下)?)|'
        r'^\s*(?:(?:please|(?:can|could|would) you|i (?:want|need) you to)\s+)*'
        r'(?:(?:research|investigate)\b|(?:perform|do|conduct)\s+(?:an?\s+)?(?:in-depth|comprehensive|detailed|multi-source)\s+(?:analysis|research|investigation)|'
        r'(?:analy[sz]e|compare|examine)\b[^.!?\n]{0,100}\b(?:in depth|in-depth|multiple sources|primary sources|across sources|comprehensively)\b)', text, re.I))


CHAT_EXECUTION_POLICY = '''Choose the depth needed to answer the user's actual question.
Start with the current conversation, reviewed reports and dated saved evidence.
A concept explanation or a supported follow-up can be answered directly; mentioning
ETH, news or a report alone does not require a new investigation. A small missing
fact may need just one focused lookup.
When resolving the question needs multiple sources, comparison, unresolved event
status or new analysis, read the research skill and use a short proportional plan.
Use the available evidence-reader for coherent, substantial source-reading tasks;
its availability does not require delegation on every turn. The initial chat hint
does not prohibit research. Reuse saved work and keep source dates and gaps visible.
Newly collected facts and new calculations retain the normal source-review
requirement. Publish a report only for an explicit user request.'''

SAVED_RESEARCH_START_POLICY = '''When a report already has an asset/window and
registered measurements, start the substantive evidence work with date-scoped
news recall; read the named calculation or contract directly as needed. Do not
rediscover the supplied scope or make mapping storage structures a plan stage.
A source catalog is a locator for a needed ID, file or exact field, not a reading
list or a prerequisite census of the corpus. Use retrieval coverage, dates,
duplicate groups and gaps to decide the next focused question. Inspect additional
metadata or sources only when they can change a material claim or limitation.
Do not enumerate every article, forum thread, provider or collection to establish
coverage. If retrieval coverage is weak, refine the missing date/topic query or
disclose the gap. Availability does not establish completeness.
Use a short plan for remaining evidence questions, synthesis and source-checked
delivery. Once acquired evidence supports the requested depth, compose the report
and address concrete validation feedback rather than continuing general discovery.'''

RESEARCH_READER_POLICY = '''For a multi-document report or investigation, separate
source selection and synthesis from substantial original-document reading. Keep
broad topic/date coverage and final judgments in the main agent. Prefer
evidence-reader for a specific evidence question that needs multiple long
documents, comparison of announcements, qualifications or conflicting updates.
For example, ask whether an upgrade's later notice changes its original plan;
do not assign an entire protocol ecosystem or a list of unrelated forum themes.
Delegate the public question to settle, asset/time/cutoff scope and prioritized
canonical source/passage IDs already found by recall. A thread URL or title is
navigation; include saved record IDs when available instead of asking the worker
to rediscover the whole inventory. Request only findings material to that question.
Do not send personal context or one task per chunk.
The worker may retrieve and read additional relevant saved passages for its theme.
Reuse its concise findings, exact quotations, source pointers, qualifications,
conflicts and gaps; intermediate reading stays in the worker's context. Do not
repeat the same originals in the main agent. Reopen only a missing qualification,
an unresolved conflict or a specific spot check. Main-agent reading is suitable
for a few short sources and straightforward facts; delegation is not a quota.
Compare each finding's literal event-status predicate with its source qualifier;
a cautious summary does not fix an overstated key change or interpretation.
Preserve entity/asset/network/venue, what a quantity measures, stock versus flow,
its window/baseline and publication versus event/acquisition time in each finding.
An in-window publication may report a preceding-period quantity; preserve that
measurement interval. Keep an in-window historical plan separate from an explicit
post-window correction on the same captured page.
Inspect relevant qualifications and contradictory figures elsewhere in the same
article or other read sources; do not select one unresolved version as settled.
A source's silence is not evidence of a whole-window absence or globally unset
date. Preserve the counted object and source authority; a captured news article
can be secondary reporting. Do not infer native-asset buying from permissioned
settlement or parent-platform turnover.
Reader findings are derived analysis. Cite original evidence IDs and retain all
publication checks. Ranking and a small result count do not establish coverage;
fill specific material gaps or disclose them rather than repeating broad reads.'''
