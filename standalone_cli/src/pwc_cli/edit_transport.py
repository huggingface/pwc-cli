"""Origin-bound edit transport. Never forwards a credential through a redirect."""

import json
import urllib.error
import urllib.parse
import urllib.request

from pwc_cli.transport import (
    HTTPStatusError,
    MAX_RESPONSE_BYTES,
    ResponseError,
    TransportError,
)
from pwc_cli import __version__


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def edit_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ResponseError(
            "Edit API URL must not contain credentials, query, or fragment"
        )
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ResponseError(
            "Editing requires HTTPS (HTTP is allowed only on loopback for development)"
        )
    if not parsed.hostname:
        raise ResponseError("Invalid edit API origin")
    return value.rstrip("/")


class EditClient:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = edit_base_url(base_url)
        self.token = token

    def request(self, method: str, path: str, payload=None):
        if not path.startswith("/") or path.startswith("//") or ".." in path:
            raise ResponseError("Invalid edit endpoint")
        headers = {"Accept": "application/json", "User-Agent": f"pwc-cli/{__version__}"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, method=method, data=data, headers=headers
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=60
            ) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ResponseError("Edit response exceeded the byte limit")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raise HTTPStatusError(
                error.code, error.read(4096).decode("utf-8", errors="replace")
            ) from error
        except urllib.error.URLError as error:
            raise TransportError(f"Edit API request failed: {error.reason}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResponseError("Edit API returned invalid JSON") from error
