"""
Serve the umpire map: static UI + FastAPI simulation backend.
"""
from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
PORT = 8765


def main() -> None:
    os.chdir(ROOT)
    url = f"http://127.0.0.1:{PORT}/index.html"
    print(f"Serving {ROOT} at {url}")
    print("API: /api/bootstrap, /api/sim/tick, …")
    threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "backend.api:app",
        host="127.0.0.1",
        port=PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
