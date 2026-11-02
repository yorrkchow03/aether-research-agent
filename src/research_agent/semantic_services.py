"""Opt-in production construction for optional semantic judgments.

The default keeps the existing service and cache unchanged. Selecting the new
policy adds no tool, warmup, call, deployment or settings mutation: provider I/O
still happens only through a later explicit assess() invocation by the harness.
"""
from pathlib import Path
from uuid import uuid4

from .evidence_judgments import EvidenceJudgmentService
from .selective_judgments import SelectiveJudgmentService

POLICIES = frozenset({'legacy', 'selective'})


def build_judgment_service(root,state_dir,*,provider='jev',policy='legacy'):
    """Build one job's optional service; reuse only source-bound annotations.

    Legacy/non-Jev callers retain EvidenceJudgmentService exactly. A selective
    Jev caller receives a fresh SelectiveJudgmentService with task-local dynamic
    stores and separate persistent classification-only stores. The returned
    selective service exposes assess()/metrics(); legacy accounting continues
    through the existing sidecar/provider receipts.
    """
    if policy not in POLICIES:raise ValueError('Unknown optional judgment policy')
    root,state_dir=Path(root),Path(state_dir)
    if policy=='legacy' or provider!='jev':
        return EvidenceJudgmentService(root,state_dir/'evidence-judgments',provider=provider,
            timeout_seconds=12,max_attempts=1)
    base=state_dir/'evidence-judgments-selective'
    dynamic=base/'tasks'/uuid4().hex
    def service(name,path):
        return EvidenceJudgmentService(root,path,provider=name,
            requested_model='jev-1.14.0' if name=='jev' else 'doubao-seed-2.1-pro',
            timeout_seconds=30,max_attempts=1,minimum_confidence=.8)
    return SelectiveJudgmentService(service('jev',dynamic/'jev'),service('doubao',dynamic/'pro'),
        classification_primary=service('jev',base/'classification-jev'),
        classification_pro=service('doubao',base/'classification-pro'),
        primary_limit=12,pro_limit=16,minimum_confidence=.8)
