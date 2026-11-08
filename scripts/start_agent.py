"""Start the personal loopback application using local private configuration."""
import argparse
import uvicorn
from research_agent.server import create_app

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    uvicorn.run(create_app(port=args.port),host='127.0.0.1',port=args.port,log_level='warning')

if __name__=='__main__':main()
