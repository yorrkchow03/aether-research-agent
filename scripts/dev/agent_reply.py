"""Submit a current-conversation reply to a waiting local Deep Agents round."""
import argparse
import json
from pathlib import Path
from research_agent.bridge import write_reply

p=argparse.ArgumentParser()
p.add_argument("request",type=Path)
p.add_argument("reply",type=Path,help="JSON containing content and tool_calls; produced by the current conversation")
a=p.parse_args()
print(write_reply(a.request,json.loads(a.reply.read_text())))
