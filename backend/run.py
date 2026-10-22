import os
import asyncio
import sys
import uvicorn
from .config import load_config

if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    else:
        os.umask(0o077)
    config = load_config()
    print(f"Codex Web listening on: http://{config['host']}:{config['port']}",flush=True)
    uvicorn.run('backend.main:app',host=config['host'],port=config['port'],workers=1,proxy_headers=False,loop='asyncio')
