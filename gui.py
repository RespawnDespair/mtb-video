"""Launch the Ride Highlight Editor GUI (local web app)."""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="Ride Highlight Editor GUI")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Ride Highlight Editor → {url}")
    uvicorn.run("gui.server:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
