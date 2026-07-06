"""One-time Strava OAuth2 helper.

Runs a temporary localhost server, opens the browser for you to authorize the
app, catches the redirect, exchanges the code for tokens, and writes them to
.strava_token.json (the same file strava_client.py reads and auto-refreshes).

Prerequisites (see README):
- A Strava API app registered at https://www.strava.com/settings/api with
  "Authorization Callback Domain" set to: localhost
- Environment variables:
      export STRAVA_CLIENT_ID=<your_client_id>
      export STRAVA_CLIENT_SECRET=<your_client_secret>

Usage:
    python strava_auth.py            # uses port 8000
    python strava_auth.py --port 8721
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from strava_client import TOKEN_FILE, _OAUTH, _save_token

_AUTHORIZE = "https://www.strava.com/oauth/authorize"
# activity:read_all is needed to read stats of private activities too.
_SCOPE = "activity:read_all"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the ?code= (or ?error=) from Strava's redirect."""

    # Set by the server loop; populated on the request that carries the code.
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params or "error" in params:
            _CallbackHandler.result = {k: v[0] for k, v in params.items()}
            ok = "code" in params
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = ("Authorization complete — you can close this tab and return "
                   "to the terminal." if ok else
                   f"Authorization failed: {_CallbackHandler.result.get('error')}")
            self.wfile.write(f"<html><body><h3>{msg}</h3></body></html>".encode())
        else:
            # Ignore incidental requests (e.g. /favicon.ico) without ending the wait.
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # silence the default stderr logging
        pass


def _require_credentials() -> tuple[str, str]:
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set.\n"
            "    export STRAVA_CLIENT_ID=<your_client_id>\n"
            "    export STRAVA_CLIENT_SECRET=<your_client_secret>"
        )
    return client_id, client_secret


def authorize(port: int = 8000) -> dict:
    """Run the full one-time OAuth flow and write .strava_token.json.

    Returns the saved token dict (access_token, refresh_token, expires_at).
    """
    client_id, client_secret = _require_credentials()
    redirect_uri = f"http://localhost:{port}/exchange_token"

    authorize_url = f"{_AUTHORIZE}?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "auto",
        "scope": _SCOPE,
    })

    server = HTTPServer(("localhost", port), _CallbackHandler)
    print(f"Opening browser to authorize Strava access...\n  {authorize_url}\n")
    print(f"Waiting for the redirect on {redirect_uri} ...")
    if not webbrowser.open(authorize_url):
        print("(Could not open a browser automatically — paste the URL above manually.)")

    # Handle requests until the callback with a code/error arrives.
    while not _CallbackHandler.result:
        server.handle_request()
    server.server_close()

    result = _CallbackHandler.result
    if "error" in result:
        raise SystemExit(f"Authorization was denied or failed: {result['error']}")

    resp = requests.post(_OAUTH, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    token = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
    }
    _save_token(token)

    athlete = data.get("athlete") or {}
    who = (f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}").strip()
    print(f"\nSaved tokens to {TOKEN_FILE}" + (f" for {who}." if who else "."))
    print("You can now run the pipeline with --strava.")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time Strava OAuth2 setup.")
    parser.add_argument("--port", type=int, default=8000,
                        help="Local port for the callback server (default: 8000).")
    args = parser.parse_args()
    authorize(args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
