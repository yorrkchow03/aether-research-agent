"""Original PDF ingestion, digital-text scope, and primary-page provenance."""
import tempfile,unittest
from pathlib import Path
from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from research_data.library.store import ResearchLibrary
from research_data.library.pdf_reports import extract_pdf,collect_report
from research_data.library.text import reconcile_discovery

def pdf_bytes(blank=False):
 writer=PdfWriter()
 for i in range(2):
  page=writer.add_blank_page(612,792)
  if not blank:
   font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
   page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
   stream=DecodedStreamObject();stream.set_data(b'BT /F1 10 Tf 20 750 Td ('+b'Original publisher research, source-linked digital text. '*10+b') Tj ET')
   page[NameObject('/Contents')]=writer._add_object(stream)
 out=BytesIO();writer.write(out);return out.getvalue()

class PDFReportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.l=ResearchLibrary(Path(self.tmp.name)/'library')
 def test_text_scope_is_page_labelled_and_scan_or_html_is_never_full_text(self):
  text,meta=extract_pdf(pdf_bytes());self.assertIn('[Page 1]',text);self.assertIn('[Page 2]',text);self.assertEqual(meta['page_count'],2);self.assertFalse(meta['ocr_performed'])
  for raw in (b'<html>challenge</html>',pdf_bytes(blank=True)):
   with self.assertRaises(ValueError):extract_pdf(raw)
 def test_public_attachment_keeps_parent_date_and_is_not_quarantined_as_navigation(self):
  url='https://assets.ctfassets.net/fixture/report.pdf';raw=pdf_bytes();cap=self.l.capture('coinbase_institutional',url,raw)
  parent_cap=self.l.capture('coinbase_institutional','https://www.coinbase.com/institutional/research-insights/research/report/test',b'HTML')
  parent_id=self.l.put_document('coinbase_institutional','https://www.coinbase.com/institutional/research-insights/research/report/test','Report landing','Publisher report summary',parent_cap,published_at='2026-10-07T04:00:00Z',metadata={'publication_metadata':{'publisher_primary':{'download_url':url,'download_is_public_direct':True}}})
  parent=self.l.evidence(parent_id)
  class HTTP:
   def fetch(self,*args):return cap,raw
  a=collect_report(self.l,parent,HTTP());b=collect_report(self.l,parent,HTTP());self.assertEqual(a,b)
  reconcile_discovery(self.l);row=self.l.evidence(a['record_id'])
  self.assertEqual(row['published_at'],parent['published_at']);self.assertEqual(row['source_kind'],'external_report_pdf');self.assertFalse(self.l.exclusion_reasons(row['record_id']))
  self.assertEqual(self.l.search_documents(kind='external_report_pdf')['total'],1)
  self.assertTrue(any(r['predicate']=='report_attachment_of' for r in self.l.record_links(row['record_id'])['results']))

if __name__=='__main__':unittest.main()
