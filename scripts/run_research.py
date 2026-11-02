"""Run the local harness directly using its configured model; no Codex harness."""
import argparse
import json
import time
from pathlib import Path
from research_agent.runtime import AgentRuntime

p=argparse.ArgumentParser()
p.add_argument("--prompt",required=True)
p.add_argument("--title",default="ETH 研究")
p.add_argument("--historical",action="store_true")
p.add_argument("--session",help="Continue a saved local session")
a=p.parse_args()
runtime=AgentRuntime()
session=runtime.snapshot(a.session)["session"] if a.session else runtime.create_session(title=a.title,research_mode="historical" if a.historical else "current")
job=runtime.start(session["session_id"],a.prompt)
print(json.dumps({"session_id":session["session_id"],"job_id":job["job_id"],"model":session["model_id"]},ensure_ascii=False),flush=True)
previous=None;last_log=0
while True:
 snap=runtime.snapshot(session["session_id"]);status=snap["jobs"][-1]
 event_file=runtime._session_dir(session["session_id"])/"jobs"/job["job_id"]/"events.jsonl"
 events=[json.loads(x) for x in event_file.read_text().splitlines()] if event_file.exists() else []
 latest=(status["state"],events[-1].get("kind") if events else None,events[-1].get("name") if events else None)
 if latest!=previous or time.monotonic()-last_log>20:
  print(json.dumps({"state":status["state"],"latest_event":events[-1] if events else None},ensure_ascii=False),flush=True)
  previous=latest;last_log=time.monotonic()
 if status["state"] in ("completed","failed","cancelled"):
  print(json.dumps({"result":status,"reports":snap["reports"]},ensure_ascii=False,indent=2),flush=True)
  break
 time.sleep(.5)
