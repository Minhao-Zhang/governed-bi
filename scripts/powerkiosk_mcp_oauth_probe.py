"""Power Kiosk MCP — OAuth (Cognito) connectivity probe.

**Not a production connector.** This is Phase 0 item #4 of the deployment-targets
roadmap (Obsidian: utku-ai-deployment-targets.md): the smallest thing that proves
we can reach Peruz's MCP server at all, before designing the real MCP-client
`Connector`. It does the full Authorization Code + PKCE dance against their
Cognito user pool and, on success, calls the MCP endpoint's `initialize` +
`tools/list` to prove the token actually works against the real server.

**Discovered via the MCP Authorization spec's own metadata endpoints** (RFC 9728 /
RFC 8414 — no guessing):

    GET https://mssql-mcp.powerkiosk.com/mcp                       -> 401, WWW-Authenticate
    GET .../.well-known/oauth-protected-resource/mcp                -> authorization_servers
    GET <that>/.well-known/openid-configuration                     -> authorize/token endpoints

Authorization server: AWS Cognito, user pool `us-east-2_0qzMCBGDh`, region
`us-east-2`. Token endpoint auth: `client_secret_basic` (confidential client —
matches Peruz having given us both a client id and a client secret).

**Credentials**: read from the environment / `.env` only (`POWERKIOSK_MCP_*`,
loaded via `tools/credentials.py::load_into_environ`) — never hardcoded here,
never logged. This script prints neither the password nor the client secret nor
any issued token.

**The human step this script cannot do for you**: Cognito's hosted-UI login page
requires a real browser. Run this script, open the printed URL yourself, log in
with the account Power Kiosk gave us (`POWERKIOSK_MCP_USERNAME` in `.env`), and
the local callback server below catches the redirect. This will keep failing with
`redirect_uri_mismatch` until Power Kiosk adds `--redirect-uri` (default
http://localhost:8765/callback) to the Cognito app client's **Allowed callback
URLs**.

Usage::

    uv run python scripts/powerkiosk_mcp_oauth_probe.py
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

MCP_HOST_ENV = "POWERKIOSK_MCP_HOST"
CLIENT_ID_ENV = "POWERKIOSK_MCP_CLIENT_ID"
CLIENT_SECRET_ENV = "POWERKIOSK_MCP_CLIENT_SECRET"

COGNITO_DOMAIN = "us-east-20qzmcbgdh.auth.us-east-2.amazoncognito.com"
AUTHORIZE_ENDPOINT = f"https://{COGNITO_DOMAIN}/oauth2/authorize"
TOKEN_ENDPOINT = f"https://{COGNITO_DOMAIN}/oauth2/token"
MCP_SCOPE = "https://mssql-mcp.powerkiosk.com/mcp/read"

_received_code: dict[str, str] = {}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _received_code["code"] = params["code"][0]
            body = b"<html><body>Login received. You can close this tab.</body></html>"
        else:
            _received_code["error"] = params.get("error_description", params.get("error", ["unknown"]))[0]
            body = b"<html><body>Login failed - see the terminal.</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence default access log
        pass


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _exchange_code_for_token(
    code: str, redirect_uri: str, client_id: str, client_secret: str, verifier: str
) -> dict:
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }
    ).encode()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _call_mcp(mcp_host: str, access_token: str) -> None:
    """`initialize` then `tools/list` over MCP's Streamable HTTP transport -- the
    smallest real proof the token actually authorizes something, not just that
    the OAuth dance completed."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_req = urllib.request.Request(
        mcp_host,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "powerkiosk-mcp-probe", "version": "0.1"},
                },
            }
        ).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(init_req) as resp:
        print("initialize ->", resp.status, resp.read()[:500])

    tools_req = urllib.request.Request(
        mcp_host,
        data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(tools_req) as resp:
        print("tools/list ->", resp.status, resp.read()[:2000])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--callback-path", default="/callback")
    args = parser.parse_args()

    import credentials  # noqa: E402 - sys.path adjusted above

    credentials.load_into_environ()
    mcp_host = credentials.secret(MCP_HOST_ENV)
    client_id = credentials.secret(CLIENT_ID_ENV)
    client_secret = credentials.secret(CLIENT_SECRET_ENV)

    redirect_uri = f"http://localhost:{args.port}{args.callback_path}"
    verifier, challenge = _pkce_pair()

    server = http.server.HTTPServer(("localhost", args.port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    authorize_url = AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": f"openid {MCP_SCOPE}",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    print(f"redirect_uri registered with Power Kiosk must be exactly: {redirect_uri}")
    print(f"Open this URL in a browser and log in as {credentials.secret('POWERKIOSK_MCP_USERNAME')}:\n")
    print(authorize_url)
    print("\nWaiting for the redirect...", flush=True)

    thread.join(timeout=180)
    server.server_close()

    if "error" in _received_code:
        print(f"OAuth error: {_received_code['error']}", file=sys.stderr)
        sys.exit(1)
    if "code" not in _received_code:
        print("Timed out waiting for the browser redirect.", file=sys.stderr)
        sys.exit(1)

    tokens = _exchange_code_for_token(_received_code["code"], redirect_uri, client_id, client_secret, verifier)
    print(f"token exchange ok, scopes: {tokens.get('scope')!r}, expires_in: {tokens.get('expires_in')}s")

    _call_mcp(mcp_host, tokens["access_token"])


if __name__ == "__main__":
    main()
