"""Primary publisher dates/downloads cannot come from related items/CMS clocks."""
import json,unittest
from research_data.library.coinbase_metadata import primary_metadata

URL='https://www.coinbase.com/institutional/research-insights/research/report/current'
def source(fields,others=None):
 state={'suspenseBridgeData':json.dumps([{'data':{'page':{'fields':fields},'related':others or []}}])}
 return ('<script id="server-app-state" type="application/json">'+json.dumps(state)+'</script>').encode()

class PublisherMetadataTests(unittest.TestCase):
 def test_primary_slug_and_explicit_date_override_cms_creation_and_related_dates(self):
  fields={'slug':'current','title':'Current report','publicationDate':'2026-10-07T00:00-04:00','sys':{'createdAt':'2026-10-13T00:00:00Z'},'content':{'fields':{}}}
  other={'slug':'another','publicationDate':'2030-01-01','content':{}}
  r=primary_metadata(source(fields,[other]),URL)
  self.assertEqual(r['published_at'],'2026-10-07T04:00:00+00:00');self.assertEqual(r['status'],'matched_primary_page')
 def test_missing_publication_never_uses_subject_year_or_cms_timestamp(self):
  fields={'slug':'current','title':'2026 survey','sys':{'createdAt':'2026-10-01'},'content':{}}
  self.assertNotIn('published_at',primary_metadata(source(fields),URL))
 def test_only_primary_download_flag_enables_direct_access(self):
  fields={'slug':'current','publicationDate':'2026-10-07','content':{'fields':{'pageInformation':{'fields':{'download':{'fields':{'easyDownload':True,'bynderUrl':'https://assets.ctfassets.net/public.pdf'}}}}}}}
  r=primary_metadata(source(fields),URL);self.assertTrue(r['download_is_public_direct'])
  fields['content']['fields']['pageInformation']['fields']['download']['fields']['easyDownload']=False
  self.assertFalse(primary_metadata(source(fields),URL)['download_is_public_direct'])
 def test_unrelated_deep_richtext_does_not_hide_primary_publication(self):
  nested={'value':'unrelated'}
  for _ in range(80):nested={'content':nested}
  fields={'slug':'current','publicationDate':'2026-10-07T00:00-04:00','content':nested}
  self.assertEqual(primary_metadata(source(fields),URL)['published_at'],'2026-10-07T04:00:00+00:00')

if __name__=='__main__':unittest.main()
