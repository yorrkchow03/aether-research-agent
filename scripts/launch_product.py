"""Double-click launcher for the personal loopback web app."""
import threading
import webbrowser
import httpx
import uvicorn
from research_agent.server import create_app
url='http://127.0.0.1:8765/'
try:
 with httpx.Client(trust_env=False,timeout=2) as client:
  response=client.get(url+'api/config')
  existing=response.status_code==200 and response.json().get('harness')=='Deep Agents 0.7.23'
except (httpx.HTTPError,ValueError):existing=False
if existing:
 webbrowser.open(url)
 print('Aether 已在运行：'+url)
else:
 threading.Timer(1.5,lambda:webbrowser.open(url)).start()
 print('Aether 本地服务：'+url+'；保持此终端开启。Ctrl+C 停止。')
 uvicorn.run(create_app(),host='127.0.0.1',port=8765,log_level='warning')
