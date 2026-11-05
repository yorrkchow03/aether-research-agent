"""Fixed, zero-model lexical diagnostics and input-only relation scheduling.

Rules are global public keyword/overlap rules, not a semantic truth checker.
The required ``probabilities`` fields are one-hot deterministic rule scores;
neither those scores nor confidence 1 represent calibrated correctness.
No provider, credential, source store, or reference-label file is consulted.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import re
import time
import unicodedata

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .evidence_judgments import QUESTIONS


VERSION = 'fixed-lexical-rules-v1'
SCORE_KIND = 'deterministic_rule_not_calibrated_probability'
STOP = frozenset(('a an the this that these those what which who when where how does do did is are was were '
                  'be been being to of in on at by for from with as and or it its their they them according '
                  'supplied source sources material excerpt text establishes establish report reports says '
                  'say described specifically itself every substantive part').split())
NEGATIONS = re.compile(r"\b(?:not|never|no|cannot|can't|didn't|hasn't|wasn't|isn't|aren't|won't)\b|没有|未曾|并未|并不|尚未|不是|不得|不会", re.I)
ASSETS = {
    'ETH': re.compile(r'\b(?:ethereum|ether|eth|eip-?\d+|erc-?\d+)\b|以太坊|以太币', re.I),
    'BTC': re.compile(r'\b(?:bitcoin|btc|satoshi)\b|比特币', re.I),
}
# Precedence is fixed before evaluation. Topic ambiguity is a known limitation.
TOPICS = (
    ('security', r'\b(?:vulnerabilit\w*|exploit\w*|cyberattack\w*|malware|security incident|bug bounty)\b|漏洞|黑客|安全事故|网络攻击'),
    ('macro', r'\b(?:monetary policy|interest rates?|federal reserve|fomc|inflation|central bank|economic outlook)\b|货币政策|美联储|央行|通货膨胀|利率'),
    ('regulation', r'\b(?:regulat\w*|securities and exchange commission|sec|legal|court|legislation|custody rule\w*)\b|监管|法规|法律|证券交易委员会|法院'),
    ('governance', r'\b(?:governance|snapshot vote|treasury|dao|onboard\w*|supply.cap|risk parameters?)\b|治理|提案|投票|财库|供应上限'),
    ('staking', r'\b(?:staking|staked|validator rewards?|staking economics)\b|质押|验证者收益'),
    ('defi', r'\b(?:defi|decentralized exchange\w*|lending|liquidity|automated market maker|aave|uniswap)\b|去中心化金融|借贷|流动性'),
    ('network_upgrade', r'\b(?:upgrade\w*|testnet|mainnet|scaling|consensus|client release|protocol development|eip-?\d+)\b|网络升级|测试网|主网|扩容|客户端'),
    ('market', r'\b(?:market|prices?|trading|derivatives?|etf\w*|exchange flows?|bitcoin|buyback|outperform\w*)\b|行情|价格|交易|衍生品|基金|回购'),
)
PLANNED = r'\b(?:propos\w*|scheduled|planned|pending approval|will take place|to be held)\b|拟议|提议|计划|拟于|安排在|待批准'
DELAYED = r'\b(?:postpon\w*|delayed|rescheduled)\b|推迟|延期|延后'
CANCELLED = r'\b(?:cancelled|canceled|withdrawn|called off)\b|取消|撤回'
OCCURRED = r'\b(?:occurred|activated|completed|executed|launched|released|took place|ran from|gathered)\b|已发生|已激活|已完成|已执行|已启动|举行了'
INJECTION = re.compile(r'\b(?:system override|ignore (?:all |the |previous |user )?(?:instructions?|rules?|question)|disclose (?:your |the )?api key|reveal (?:your |the )?(?:secret|api key))\b|忽略.{0,12}(?:指令|规则|问题)|(?:泄露|公开).{0,8}(?:密钥|口令)', re.I)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split()).strip(' .。!！?？')


def _terms(text):
    # Keep polarity as its own feature rather than allowing "not" to dominate
    # topic overlap. Removing a Chinese negator also preserves adjacent words.
    content = NEGATIONS.sub('', text)
    words = {word for word in re.findall(r'[a-z][a-z0-9_-]*', content.casefold())
             if len(word) > 1 and word not in STOP and not NEGATIONS.fullmatch(word)}
    for run in re.findall(r'[\u3400-\u9fff]+', content):
        words.update(run[i:i+2] for i in range(len(run)-1))
    return words


def _numbers(text):
    return {re.sub(r'[,\s]', '', number) for number in re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?:\s*%)?', text)}


def _positive(pattern, text):
    for match in re.finditer(pattern, text, re.I):
        prefix = text[max(0, match.start()-35):match.start()]
        if not re.search(r"\b(?:not|never|no)\s+(?:\w+\s+){0,2}$|(?:并未|尚未|没有|未曾|不会|未)$", prefix, re.I):
            return True
    return False


def _asset_set(text):
    return {name for name, pattern in ASSETS.items() if pattern.search(text)}


def _cosines(texts, query=''):
    if not texts:
        return np.zeros((0, 0)), np.zeros(0)
    try:
        matrix = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), max_features=12000).fit_transform([*texts, query or ' '])
        similarities = np.clip((matrix[:-1] @ matrix[:-1].T).toarray(), 0, 1)
        relevance = np.clip((matrix[:-1] @ matrix[-1].T).toarray().ravel(), 0, 1) if query.strip() else np.zeros(len(texts))
        return similarities, relevance
    except ValueError:
        return np.eye(len(texts)), np.zeros(len(texts))


def _evidence(evidence):
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or len(evidence) > 150:
        raise ValueError('Supply up to150 frozen evidence records')
    rows = []
    for index, row in enumerate(evidence):
        if not isinstance(row, Mapping):
            raise ValueError('Evidence records must be mappings')
        text = row.get('text', row.get('excerpt', ''))
        if not isinstance(text, str):
            raise ValueError('Evidence text must be a string')
        rid = row.get('record_id', row.get('id', 'input-'+str(index)))
        if not isinstance(rid, str) or not rid:
            raise ValueError('Evidence IDs must be nonempty strings')
        source = row.get('source') if isinstance(row.get('source'), Mapping) else {}
        rows.append({'record_id': rid, 'text': text, 'title': str(row.get('title') or ''),
                     'published_at': row.get('published_at'),
                     'source': {key: source[key] for key in ('name', 'url', 'sha256') if key in source}})
    return rows


def _choice(task, dimension, label=None, *, reason, triggered=False):
    criteria = QUESTIONS[task][dimension]['criteria']
    fallback = 'uncertain' if 'uncertain' in criteria else 'insufficient'
    selected = label or fallback
    if selected not in criteria:
        raise ValueError('A fixed rule produced an invalid criterion')
    return {'type': 'choice', 'choice': selected,
            'confidence': 1.0 if triggered else 0.0,
            'probabilities': {key: float(key == selected) for key in criteria},
            'score_kind': SCORE_KIND, 'rule_triggered': triggered, 'rule_reason': reason}


def _classification(text):
    topic, topic_reason = None, 'no_fixed_topic_keyword'
    for label, pattern in TOPICS:
        if re.search(pattern, text, re.I):
            topic, topic_reason = label, 'fixed_topic_keyword:'+label
            break
    assets = _asset_set(text)
    asset = 'both' if len(assets) == 2 else next(iter(assets)) if assets else None
    if asset is None and (topic in ('macro', 'regulation') or re.search(r'\b(?:solana|sol|xrp|dogecoin)\b|其他资产', text, re.I)):
        asset = 'other'
    status, status_reason = None, 'no_explicit_fixed_event_status'
    for label, pattern in (('cancelled', CANCELLED), ('delayed', DELAYED), ('planned', PLANNED), ('occurred', OCCURRED)):
        if _positive(pattern, text):
            status, status_reason = label, 'fixed_event_status_keyword:'+label
            break
    return {'asset': _choice('classification', 'asset', asset, reason='fixed_asset_keywords' if asset else 'no_fixed_asset_keyword', triggered=asset is not None),
            'topic': _choice('classification', 'topic', topic, reason=topic_reason, triggered=topic is not None),
            'event_status': _choice('classification', 'event_status', status, reason=status_reason, triggered=status is not None)}


def _relevance(question, text):
    label, reason = None, 'insufficient_lexical_signal'
    if question.strip() and len(text.strip()) >= 20:
        _, similarities = _cosines([text], question)
        cosine = float(similarities[0])
        query_terms = _terms(question)
        coverage = len(query_terms & _terms(text)) / len(query_terms) if query_terms else 0
        question_assets, source_assets = _asset_set(question), _asset_set(text)
        if question_assets and source_assets and not question_assets.intersection(source_assets):
            label, reason = 'irrelevant', 'disjoint_explicit_asset_keywords'
        elif cosine >= .12 or coverage >= .2:
            label, reason = 'relevant', 'fixed_char_tfidf_or_query_term_overlap'
        elif cosine >= .04:
            label, reason = 'background', 'weak_char_tfidf_overlap'
    risk = 'present' if INJECTION.search(text) else 'absent' if text.strip() else None
    return {'relevance': _choice('relevance', 'relevance', label, reason=reason, triggered=label is not None),
            'instruction_risk': _choice('relevance', 'instruction_risk', risk,
                                        reason='fixed_injection_pattern' if risk == 'present' else 'no_fixed_injection_pattern' if risk else 'empty_source',
                                        triggered=risk is not None)}


def _scope(text):
    # Only explicit disjoint singleton network scopes justify this rule.
    found = set(re.findall(r'\b(?:mainnet|testnet|hoodi|sepolia|goerli)\b|主网|测试网', text, re.I))
    return {value.casefold() for value in found}


def _polarity_or_number_contradiction(claim, sentence):
    left, right = _terms(claim), _terms(sentence)
    if len(left) < 2 or len(right) < 2:
        return None
    overlap = len(left & right) / max(len(left), len(right))
    if overlap >= .8 and bool(NEGATIONS.search(claim)) != bool(NEGATIONS.search(sentence)):
        return 'matched_terms_opposite_explicit_negation'
    a, b = _numbers(claim), _numbers(sentence)
    if overlap >= .8 and len(a) == len(b) == 1 and a != b:
        return 'matched_terms_different_single_explicit_number'
    return None


def _relation(rows):
    label, reason = None, 'no_conservative_relation_rule'
    if len(rows) == 2 and all(row['text'].strip() for row in rows):
        a, b = rows[0]['text'], rows[1]['text']
        similarity = float(_cosines([a, b])[0][0, 1])
        a_scope, b_scope = _scope(a), _scope(b)
        a_assets, b_assets = _asset_set(a), _asset_set(b)
        if _normalized(a) == _normalized(b):
            label, reason = 'duplicate', 'exact_casefold_whitespace_normalized_text'
        elif (a_assets and b_assets and not a_assets.intersection(b_assets)):
            label, reason = 'different_scope', 'disjoint_explicit_asset_keywords'
        elif len(a_scope) == len(b_scope) == 1 and a_scope != b_scope:
            label, reason = 'different_scope', 'disjoint_singleton_explicit_network_scopes'
        elif re.search(r'\b(?:different|separate) (?:event|version|time|period|session|network)\b|不同(?:事件|版本|时间|时段|网络)', b, re.I):
            label, reason = 'different_scope', 'explicit_different_scope_wording'
        elif similarity >= .08 and _positive(r'\b(?:supersed\w*|corrects?|withdraws?|replaces?)\b|更正|明确撤回|取代', b):
            label, reason = 'supersedes', 'shared_lexical_context_and_explicit_correction_verb'
        elif similarity >= .12 and _positive(r'\b(?:subsequent update|later update|closing report|postpon\w*|rescheduled)\b|后续更新|最新更新|延期|推迟', b):
            label, reason = 'update', 'shared_lexical_context_and_explicit_state_update'
        elif _polarity_or_number_contradiction(a, b):
            label, reason = 'contradicts', _polarity_or_number_contradiction(a, b)
    return {'relation': _choice('relation', 'relation', label, reason=reason, triggered=label is not None)}


def _support(question, text):
    label, reason = None, 'insufficient_fixed_lexical_support'
    claim_terms = _terms(question)
    if question.strip() and text.strip() and claim_terms:
        coverage = len(claim_terms & _terms(text)) / len(claim_terms)
        if (re.search(r'\b(?:if|prospective|conditional)\b|如果|假如|若', question, re.I)
                and re.search(r'\b(?:revisit|monitor|reassess|watch|consider)\b|复核|关注|重新评估|再评估', question, re.I)
                and coverage >= .2):
            label, reason = 'conditional', 'explicit_monitoring_conditional_with_lexical_grounding'
        else:
            sentences = [sentence for sentence in re.split(r'(?<=[.!?])\s+|(?<=[。！？])|\n+', text) if sentence.strip()]
            for sentence in sentences:
                contradiction = _polarity_or_number_contradiction(question, sentence)
                if contradiction:
                    label, reason = 'contradicted', contradiction
                    break
            if label is None:
                for sentence in sentences:
                    source_terms = _terms(sentence)
                    exact = _normalized(question) in _normalized(sentence)
                    shared = len(claim_terms & source_terms) / len(claim_terms)
                    number_match = _numbers(question).issubset(_numbers(sentence))
                    polarity_match = bool(NEGATIONS.search(question)) == bool(NEGATIONS.search(sentence))
                    # Conservative literal-match diagnostic. Extra source
                    # negation or modal qualifications block token-only support.
                    source_qualified = bool(re.search(r'\b(?:may|might|could|pending|proposed|planned|scheduled)\b|可能|拟议|计划|待批准', sentence, re.I))
                    claim_qualified = bool(re.search(r'\b(?:may|might|could|pending|proposed|planned|scheduled)\b|可能|拟议|计划|待批准', question, re.I))
                    if number_match and polarity_match and (exact or (len(claim_terms) >= 3 and shared >= .95 and (not source_qualified or claim_qualified))):
                        label, reason = 'supported', 'literal_or_near_complete_term_match_with_number_negation_checks'
                        break
    return {'support': _choice('support', 'support', label, reason=reason, triggered=label is not None)}


def rule_judgment(task, *, question='', evidence):
    """Return a service-shaped fixed-rule diagnostic without model activity."""
    started = time.perf_counter()
    if task not in QUESTIONS or not isinstance(question, str):
        raise ValueError('Use a supported task and string question')
    rows = _evidence(evidence)
    text = '\n'.join(' '.join((row['title'], row['text'])).strip() for row in rows)
    dimensions = (_classification(text) if task == 'classification' else
                  _relevance(question, text) if task == 'relevance' else
                  _relation(rows) if task == 'relation' else _support(question, text))
    main = dimensions['topic' if task == 'classification' else task]
    accepted = main['rule_triggered'] and main['choice'] != 'uncertain'
    usage = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
    identity = _digest({'task': task, 'question': question, 'evidence': rows, 'rule_version': VERSION})
    return {'provider': 'rule', 'model': VERSION, 'requested_model': VERSION,
            'status': 'ok' if accepted else 'abstained', 'task': task,
            'label': main['choice'] if accepted else None,
            'labels': [name+':'+answer['choice'] for name, answer in dimensions.items() if answer['rule_triggered'] and answer['choice'] != 'uncertain'],
            'scores': main['probabilities'], 'confidence': main['confidence'],
            'dimensions': dimensions, 'score_kind': SCORE_KIND,
            'confidence_basis': 'Binary rule-trigger indicator; never a probability of correctness',
            'threshold_basis': 'Fixed rules with binary acceptance; no reference-label tuning or calibration',
            'input_hash': identity, 'cache_key': identity, 'cache_hit': False,
            'evidence_ids': [row['record_id'] for row in rows],
            'source_hashes': {row['record_id']: row['source'].get('sha256') or hashlib.sha256(row['text'].encode()).hexdigest() for row in rows},
            'latency_seconds': time.perf_counter()-started, 'usage': usage,
            'request_count': 0, 'physical_request_count': 0, 'billable_request_made': False,
            'billing': {'provider': 'rule', 'new_request_submitted': False,
                        'estimated_usd': '0', 'estimated_afp': '0', 'actual_account_debit': '0',
                        'input_tokens': 0, 'output_tokens': 0, 'complete_usage': True,
                        'scope': 'Zero model/provider requests; local CPU work only.'},
            'scope': 'Transparent fixed lexical diagnostic, not a semantic truth checker; one-hot rule scores are not calibrated probabilities.'}


def _publication_time(row):
    value = row.get('published_at')
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        return None


def propose_relation_pairs(records, query='', limit=3):
    """Rank unordered pairs by lexical similarity, then orient their A/B inputs.

    Valid publication times order A before B, breaking equal-time ties by ID.
    If either time is missing/invalid, preserve the original pair positions.
    Query relevance breaks similarity ties; no gold/event-family labels enter.
    """
    if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 30:
        raise ValueError('Use a string query and pair limit in 0..30')
    rows = _evidence(records)
    texts = [' '.join((row['title'], row['text'])).strip() for row in rows]
    similarities, relevance = _cosines(texts, query)
    pairs = []
    for left in range(len(rows)):
        if not rows[left]['text'].strip():
            continue
        for right in range(left+1, len(rows)):
            if not rows[right]['text'].strip():
                continue
            if rows[left]['record_id'] == rows[right]['record_id']:
                continue
            a_time, b_time = _publication_time(rows[left]), _publication_time(rows[right])
            indexes = [left, right]
            orientation = 'input_position_missing_or_invalid_time'
            if a_time is not None and b_time is not None:
                indexes.sort(key=lambda index: (_publication_time(rows[index]), rows[index]['record_id'], index))
                orientation = 'published_at_then_id'
            similarity = float(similarities[left, right])
            mean_relevance = float((relevance[left] + relevance[right]) / 2)
            pairs.append({'indexes': indexes,
                          'rationale': {'lexical_similarity': similarity,
                                        'mean_query_relevance': mean_relevance,
                                        'method': 'char_wb_tfidf_3_5_cosine',
                                        'orientation': orientation}})
    pairs.sort(key=lambda pair: (-pair['rationale']['lexical_similarity'],
                                 -pair['rationale']['mean_query_relevance'],
                                 tuple(sorted(rows[index]['record_id'] for index in pair['indexes'])),
                                 pair['indexes']))
    return pairs[:limit]
