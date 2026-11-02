import argparse
import uvicorn
from research_agent.server import create_app

p=argparse.ArgumentParser(description="Local Deep Agents console; model configuration is loaded privately from .env")
p.add_argument("--port",type=int,default=8765)
a=p.parse_args()
uvicorn.run(create_app(port=a.port),host="127.0.0.1",port=a.port,log_level="warning")
