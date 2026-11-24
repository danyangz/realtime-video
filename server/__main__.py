"""Run with: python -m server"""

import logging

import uvicorn

from .config import Settings

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings()
    print(f"\n  打开浏览器访问： http://localhost:{s.port}\n")
    uvicorn.run("server.app:app", host=s.host, port=s.port, log_level="info")
