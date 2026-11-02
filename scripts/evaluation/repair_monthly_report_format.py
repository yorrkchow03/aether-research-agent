"""Separate, explicitly post-pilot schema-only continuations; never replace v1."""
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from deepagents import create_deep_agent, register_harness_profile, HarnessProfile, GeneralPurposeSubagentProfile
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.messages import HumanMessage, message_to_dict, messages_from_dict
from langchain_core.tools import tool

from research_agent.monthly_report_benchmark import DiagnosticReport, SYSTEM, load, score
from research_agent.model import create_ark_model
from research_agent.runtime import TraceMiddleware, freeze_software
from research_agent.settings import Settings, PLAN_BASE_URL
from research_data.http import dumps, utcnow
from research_agent.live import atomic

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / 'local/report-baseline/monthly-v1/frozen'
OUTPUT = ROOT / 'local/report-baseline/monthly-v1/format-repairs'
FEEDBACK = '''The previous submission failed tool argument validation. Correct
only the schema errors shown in the tool response. Preserve factual meaning,
eligible evidence IDs and exact numeric fields from the original supplied input.
No correctness grade or expected answer is supplied. Include required title.
Optional sections may be omitted when not requested. Never invent a citation
to satisfy a required list; unsupported personal context belongs in limitations
and missing information. Submit the corrected draft once. No new sources.'''


def main():
    config, records, cases = load(FROZEN)
    settings = Settings.load(ROOT)
    assert settings.model_id == 'doubao-seed-2.1-pro' and settings.base_url == PLAN_BASE_URL
    if OUTPUT.exists():
        raise ValueError('A format-repair run already exists; inspect it, do not repeat')
    OUTPUT.mkdir(parents=True)
    _, fingerprint = freeze_software(ROOT, OUTPUT)
    atomic(OUTPUT / 'protocol.json', {'created_at': utcnow(), 'phase': 'post_pilot_schema_only',
        'original_manifest_sha256': __import__('hashlib').sha256((FROZEN / 'manifest.json').read_bytes()).hexdigest(),
        'feedback': FEEDBACK, 'maximum_calls_per_case': 2, 'software_fingerprint': fingerprint,
        'scope': 'Diagnostic follow-up after inspecting schema failures. Original first-pass failure rates stay unchanged. No untouched-test or fair policy-comparison claim.'})
    register_harness_profile('ark_plan:' + settings.model_id, HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({'execute', 'task', 'delete'})))
    for case in cases:
        previous = json.loads((FROZEN / case['case_id'] / 'result.json').read_text())
        if previous['state'] != 'failed' or previous.get('error') != 'No structured evaluation report submitted':
            continue
        directory = OUTPUT / case['case_id']
        (directory / 'workspace').mkdir(parents=True)
        selected = json.loads((FROZEN / case['case_id'] / 'selected_evidence.json').read_text())
        messages = messages_from_dict(json.loads((FROZEN / case['case_id'] / 'messages.json').read_text()))
        captured, events = [], []
        def emit(kind, data):
            event = {'kind': kind, 'time': utcnow(), **data}; events.append(event)
            with (directory / 'events.jsonl').open('a') as stream:
                stream.write(settings.redact(dumps(event)) + '\n')
        @tool(return_direct=True)
        def submit_benchmark_report(draft: DiagnosticReport) -> str:
            """Retain a schema-correct evaluation draft using only original eligible sources. This is not publication or semantic acceptance."""
            captured.append(draft)
            atomic(directory / 'report.json', draft.model_dump())
            return '{"status":"evaluation_draft_saved"}'
        model = create_ark_model(settings)
        start = perf_counter(); result = {'case_id': case['case_id'], 'state': 'failed'}
        try:
            graph = create_deep_agent(model=model, tools=[submit_benchmark_report], system_prompt=SYSTEM,
                backend=FilesystemBackend(root_dir=directory / 'workspace', virtual_mode=True),
                permissions=[FilesystemPermission(operations=['write'], paths=['/**'], mode='deny')],
                middleware=[TraceMiddleware(emit, settings=replace(settings, max_model_calls=2))])
            for attempt in range(2):
                state = graph.invoke({'messages': [*messages, HumanMessage(content=FEEDBACK)]},
                                     config={'recursion_limit': 20, 'max_concurrency': 1})
                atomic(directory / ('messages-' + str(attempt+1) + '.json'),
                       json.loads(settings.redact(dumps([message_to_dict(m) for m in state['messages']]))))
                if captured:
                    result.update(state='completed', score=score(captured[0], case, selected)); break
                messages = state['messages']
            if not captured:
                result['error'] = 'Schema-only repair did not yield a valid submission'
        except Exception as exc:
            result['error'] = settings.redact(exc)
        finally:
            model.http_client.close()
            calls = [e for e in events if e['kind'] == 'model_finished']
            result.update(author_calls=len(calls), latency_seconds=perf_counter()-start, dollar_cost=None,
                usage={k: sum((e.get('usage') or {}).get(k, 0) or 0 for e in calls) for k in ('input_tokens', 'output_tokens', 'total_tokens')})
            atomic(directory / 'result.json', result)
        print(dumps({k: result.get(k) for k in ('case_id', 'state', 'author_calls', 'error')}), flush=True)


if __name__ == '__main__':
    main()
