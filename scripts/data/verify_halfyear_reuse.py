"""Replay independent reference acquisitions from cache with networking forbidden."""
import json
from pathlib import Path
import httpx
from research_data.library.store import ResearchLibrary
from research_data.library.http import PublicCapture
from research_data.library.extensions import collect_bitstamp,collect_treasury

ROOT=Path(__file__).resolve().parents[2]

def main():
 l=ResearchLibrary(ROOT/'data/library-halfyear-20260426-20261026',create=False)
 def blocked(request):raise AssertionError('Unexpected network request in cache replay: '+str(request.url))
 http=PublicCapture(l,transport=httpx.MockTransport(blocked));jobs=[]
 def counts():
  with l.connect(readonly=True) as c:return {t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('captures','objects','observations','observation_captures')}
 def job(name,fn):
  details=fn();l.job(name,'collected',**details);jobs.append({'dataset_id':name,**details})
 before=counts();collect_bitstamp(l,http,job);collect_treasury(l,http,job);after=counts()
 if before!=after:raise AssertionError('Cached collection unexpectedly grew stored receipts/facts: '+str((before,after)))
 result={'passed':True,'network_forbidden':True,'before':before,'after':after,'datasets':jobs,'scope':'Replay original Bitstamp/Treasury collection from actual captured responses. No numerical/document duplication; acquisition of unrelated text may continue, so execute after other writers finish for a global-count acceptance.'}
 out=ROOT/'local/halfyear/data/CACHED_REPLAY.json';out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'passed':True,'network_forbidden':True,'datasets':len(jobs),'counts':after}))

if __name__=='__main__':main()
