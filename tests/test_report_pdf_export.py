"""PDF downloads preserve saved content, citations, and reader access boundaries."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from pypdf import PdfReader

from research_agent.live import atomic
from research_agent.report_pdf import render_report_pdf
from research_agent.runtime import AgentRuntime
from research_agent.server import create_app


REPORT = 'research-'+'a'*12
RID = 'page-'+'b'*24


def saved_report():
    return {'report': {'title': 'ETH research <example>', 'summary': 'The saved close was $2,234.61.',
            'observations': [{'statement': 'The saved close was $2,234.61.', 'evidence_ids': [RID],
                              'metric': 'close_usd', 'value': '2234.61', 'unit': 'USD'}],
            'limitations': ['Incomplete news coverage.'], 'questions_to_watch': ['Check the next official update.']},
            'created_at': '2026-11-07T00:00:00Z', 'review': {'status': 'reviewed'},
            'sources': [{'name': 'Publisher', 'url': 'https://example.org/source?a=1&b=2'}],
            'run_metadata': {'private_debug': 'DO-NOT-EXPORT-INTERNAL-STATE'}}


class ReportPDFTests(unittest.TestCase):
    def test_concurrent_exports_keep_embedded_font_and_text_bound_to_each_report(self):
        def export(number):
            packet=saved_report()
            packet['report']['title']='研究报告 '+str(number)
            pdf=render_report_pdf(packet,REPORT)
            return '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        with ThreadPoolExecutor(max_workers=3) as pool:
            texts=list(pool.map(export,range(3)))
        for number,text in enumerate(texts):
            self.assertIn('研究报告 '+str(number),text)

    def test_pdf_keeps_text_exact_values_citations_links_and_unicode_without_mutation(self):
        packet = saved_report()
        packet['report']['summary'] += ' 研究报告。'
        packet['presentation'] = {'overview': {'user_prompt': 'Review ETH', 'start': '2026-11-01T00:00:00Z',
            'end': '2026-11-03T00:00:00Z'}, 'charts': [{'title': 'Closing price', 'asset': 'ETH', 'unit': 'USD',
            'frequency': '1d', 'provider': 'fixture', 'evidence_id': RID,
            'start': '2026-11-01T00:00:00Z', 'end': '2026-11-03T00:00:00Z',
            'points': [{'at': '2026-11-02T00:00:00Z', 'value': '2234.61'},
                       {'at': '2026-11-03T00:00:00Z', 'value': '2235.12', 'gap_before': True}]}]}
        before = deepcopy(packet)
        original = {'title': 'Publisher original', 'url': packet['sources'][0]['url']}
        pdf = render_report_pdf(packet, REPORT, evidence_lookup=lambda rid: original,
                                notices=[{'note': 'A saved reader concern.'}])
        reader = PdfReader(BytesIO(pdf))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for expected in ('ETH research <example>', '2,234.61', RID,
                         'Incomplete news coverage.', 'A saved reader concern.', '研究报告'):
            self.assertIn(expected, text)
        self.assertNotIn('DO-NOT-EXPORT', text)
        fonts = [font.get_object() for page in reader.pages for font in page['/Resources']['/Font'].values()]
        self.assertTrue(any(font.get('/FontDescriptor') and
                            font['/FontDescriptor'].get('/FontFile2') for font in fonts))
        uris = [annotation.get_object().get('/A', {}).get('/URI')
                for page in reader.pages for annotation in page.get('/Annots', [])]
        self.assertIn(original['url'], uris)
        self.assertEqual(packet, before)

    def test_original_task_is_in_pdf_provenance_after_main_report(self):
        packet=saved_report();prompt='BENCHMARK-CONFIG: exact task; use tool publish_report.'
        packet['presentation']={'overview':{'user_prompt':prompt}}
        before=deepcopy(packet)
        text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(render_report_pdf(packet,REPORT))).pages)
        self.assertGreater(text.index('Technical provenance'),text.index('Limits to keep in mind'))
        self.assertGreater(text.index('BENCHMARK-CONFIG'),text.index('Evidence references'))
        self.assertIn(prompt,text)
        self.assertEqual(packet,before)

    def test_pdf_timeline_contains_dated_events_their_facts_and_their_documents(self):
        packet = saved_report()
        draft = packet['report']
        other_rid = 'page-'+'c'*24
        draft['scope'] = {'assets':['ETH'],'start':'2026-11-01T00:00:00Z','end':'2026-11-07T00:00:00Z'}
        draft['key_changes'] = [{'statement':'2026-11-05: Secondary event.','evidence_ids':[other_rid],
                                 'metric':'votes','value':'48','unit':'count'},
                                {'statement':'2026-11-02: First reported event.','evidence_ids':[RID]}]
        draft['observations'].append({'statement':'A supporting fact.','evidence_ids':[RID]})
        draft['interpretations'] = [{'statement': 'A sourced interpretation.', 'evidence_ids': [RID]}]
        draft['advice'] = {'summary': 'Investigate the next confirmed milestone.', 'context_evidence_ids': [RID]}
        packet['presentation'] = {'documents': [{'title':'Featured publisher archive','evidence_id':RID,
                                                'published_at':'2026-11-02T13:00:00Z','url':packet['sources'][0]['url'],
                                                'quote':'Exact statement from the publisher.','role':'primary'}]}

        pdf = render_report_pdf(packet, REPORT)
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        ordered = ['Summary', 'Key data charts', 'Key metric changes', 'close_usd = 2234.61 USD',
                   'Key events', 'First reported event.', 'A supporting fact.', 'Featured publisher archive',
                   'Quoted passage: Exact statement from the publisher.', 'Secondary event.', 'votes = 48 count', 'Research judgment',
                   'Interpretation', 'Conditional research suggestion', 'What to watch next',
                   'Limits to keep in mind', 'Sources, review and downloads', 'Evidence references']
        positions = [text.index(section) for section in ordered]
        self.assertEqual(positions, sorted(positions))
        for event in ('First reported event.', 'A supporting fact.', 'Secondary event.', 'Quoted passage:'):
            self.assertEqual(text.count(event), 1)
        self.assertNotIn('Important documents', text)
        self.assertNotIn('Main facts', text)

    def test_pdf_uses_neutral_title_and_caps_follow_up_items(self):
        packet = saved_report()
        draft = packet['report']
        draft['title'] = 'Limited evidence report'
        draft['delivery_kind'] = 'minimal'
        draft['questions_to_watch'] = ['Verify the next '+name+'.' for name in
                                      ('official upgrade', 'vote', 'data release', 'committee meeting', 'security review')]
        reader = PdfReader(BytesIO(render_report_pdf(packet, REPORT)))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertNotIn('Limited evidence report', text)
        self.assertNotIn('Limited evidence report', reader.metadata.title)
        self.assertEqual(text.count('Verify the next'), 3)

    def test_owner_download_is_an_attachment_and_unknown_reports_are_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = AgentRuntime(root=Path(temp), model_mode='current_conversation_no_api')
            runtime.knowledge.retain_report(REPORT, 'fixture', saved_report())
            with TestClient(create_app(runtime), base_url='http://127.0.0.1:8765') as client:
                response = client.get('/api/reports/'+REPORT+'/pdf')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['content-type'], 'application/pdf')
                self.assertIn('attachment;', response.headers['content-disposition'])
                self.assertTrue(response.content.startswith(b'%PDF-'))
                self.assertEqual(client.get('/api/reports/research-'+'0'*12+'/pdf').status_code, 404)
                self.assertEqual(runtime.sessions(), [])


if __name__ == '__main__':
    unittest.main()
