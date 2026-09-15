"""Authenticated paper edits for humans and coding agents."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import webbrowser

from pwc_cli.edit_credentials import CredentialStore
from pwc_cli.edit_transport import EditClient, edit_base_url
from pwc_cli.transport import ResponseError


class EditUsageError(ValueError):
    pass


def paper_id(reference, client):
    from pwc_cli.cli import _resolve_paper

    reference = _resolve_paper(reference, client)
    if reference.isdigit():
        return int(reference)
    value = client.get(f"papers/{reference}").json().get("id")
    if not value:
        raise ResponseError("Paper response did not include its database ID")
    return int(value)


def auth(args, client):
    base = edit_base_url(client.base_url)
    target = paper_id(args.paper, client)
    store = CredentialStore(base)
    if args.auth_command == "login":
        api = EditClient(base)
        start = api.request("POST", "/paper-edit-authorizations", {"paper_id": target})
        origin = urlsplit(base)
        path = start["verification_path"]
        if not path.startswith("/authorize-paper-edit/") or "?" in path or "#" in path:
            raise ResponseError("Invalid browser authorization path")
        url = f"{origin.scheme}://{origin.netloc}{path}"
        print(
            f"Authorize paper {target} at {url}\nCheck that the browser shows code {start['user_code']}.\nAccess expires after one hour; edits publish immediately.",
            file=sys.stderr,
        )
        if not args.no_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + min(int(start["expires_in"]), 600)
        while time.monotonic() < deadline:
            result = api.request(
                "POST",
                "/paper-edit-authorizations/token",
                {"device_code": start["device_code"]},
            )
            if result.get("status") == "authorized":
                if result.get("paper_id") != target or not result.get("access_token"):
                    raise ResponseError(
                        "Authorization returned the wrong paper or no credential"
                    )
                store.save(target, result)
                print(
                    json.dumps(
                        {
                            "status": "authorized",
                            "paper_id": target,
                            "username": result["username"],
                            "expires_at": result["expires_at"],
                        }
                    )
                )
                return 0
            if result.get("status") != "authorization_pending":
                raise ResponseError("Unexpected authorization response")
            time.sleep(max(1, min(int(start["interval"]), 10)))
        raise EditUsageError("Browser authorization timed out. Run login again.")
    saved = store.load(target)
    if args.auth_command == "status":
        print(
            json.dumps(
                {
                    "paper_id": target,
                    "api": base,
                    "username": saved["username"],
                    "expires_at": saved["expires_at"],
                    "expired": datetime.fromisoformat(saved["expires_at"])
                    <= datetime.now(timezone.utc),
                }
            )
        )
    else:
        EditClient(base, saved["access_token"]).request("POST", "/paper-edit-logout")
        store.remove(target)
        print("Paper authorization revoked.")
    return 0


def edit(args, client):
    base = edit_base_url(client.base_url)
    target = paper_id(args.paper, client)
    saved = CredentialStore(base).load(target)
    if datetime.fromisoformat(saved["expires_at"]) <= datetime.now(timezone.utc):
        raise EditUsageError(
            "Authorization expired. Run pwc auth login --paper PAPER again; keep your edit document."
        )
    api = EditClient(base, saved["access_token"])
    if args.edit_command == "export":
        document = api.request("GET", f"/papers/{target}/edit-document")
        with Path(args.output).open("x") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        print(
            f"Exported paper {target} to {args.output}. Add explicit operations; current is reference material only."
        )
        return 0
    with Path(args.file).open("rb") as handle:
        raw = handle.read(1_048_577)
    if len(raw) > 1_048_576:
        raise EditUsageError("Edit documents must be at most 1 MiB")
    document = json.loads(raw)
    if document.get("paper_id") != target:
        raise EditUsageError("Document targets a different paper")
    document.pop("current", None)
    suffix = "/preview" if args.edit_command == "preview" else ""
    result = api.request("POST", f"/papers/{target}/edits{suffix}", document)
    print(json.dumps(result, indent=2))
    return 0


def add_commands(commands, paper_commands):
    parser = commands.add_parser("auth", help="authorize one-hour edits to one paper")
    sub = parser.add_subparsers(dest="auth_command", required=True)
    for name in ("login", "status", "logout"):
        command = sub.add_parser(name)
        command.add_argument("--paper", required=True)
        if name == "login":
            command.add_argument(
                "--no-browser",
                action="store_true",
                help="print the authorization link without opening a browser",
            )
        command.set_defaults(handler=auth)
    parser = paper_commands.add_parser(
        "edit", help="export, preview, or publish an atomic paper edit"
    )
    sub = parser.add_subparsers(dest="edit_command", required=True)
    for name in ("export", "preview", "submit"):
        command = sub.add_parser(name)
        command.add_argument("paper")
        command.add_argument(
            "--output" if name == "export" else "--file", required=True
        )
        command.set_defaults(handler=edit)
