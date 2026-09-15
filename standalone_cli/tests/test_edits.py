import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from pwc_cli.edit_credentials import CredentialStore
from pwc_cli.edit_transport import EditClient, NoRedirect, edit_base_url
from pwc_cli.edits import EditUsageError
from pwc_cli import edits
from pwc_cli.cli import build_parser
from pwc_cli.transport import Client, ResponseError


def test_credential_origin_paper_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    store = CredentialStore("https://paperswithcode.co/api/v1")
    store.save(1, {"paper_id": 1, "access_token": "secret"})
    assert stat.S_IMODE(store.path(1).stat().st_mode) == 0o600
    assert store.load(1)["access_token"] == "secret"
    with pytest.raises(ResponseError):
        store.load(2)
    with pytest.raises(ResponseError):
        CredentialStore("https://other.example/api/v1").load(1)
    store.path(1).chmod(0o644)
    with pytest.raises(ResponseError):
        store.load(1)


def test_edit_transport_rejects_unsafe_origins_and_redirects():
    for url in (
        "http://example.org/api/v1",
        "https://user:pass@example.org/api/v1",
        "https://example.org/api?token=secret",
    ):
        with pytest.raises(ResponseError):
            edit_base_url(url)
    assert edit_base_url("http://127.0.0.1:8989/api/v1").startswith("http:")
    assert (
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")
        is None
    )


def test_submit_omits_reference_material_and_keeps_retry_key(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    client = Client()
    store = CredentialStore(client.base_url)
    store.save(
        1,
        {
            "paper_id": 1,
            "access_token": "top-secret",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
    )
    document = {
        "paper_id": 1,
        "version": "a" * 64,
        "idempotency_key": "stable-key",
        "current": {"private_reference": True},
        "operations": [{"section": "tasks", "payload": {"task_ids": [3]}}],
    }
    path = tmp_path / "edits.json"
    path.write_text(json.dumps(document))
    calls = []

    def request(self, method, endpoint, payload=None):
        calls.append((method, endpoint, payload))
        return {"status": "published"}

    monkeypatch.setattr(EditClient, "request", request)
    args = build_parser().parse_args(
        ["paper", "edit", "submit", "1", "--file", str(path)]
    )
    assert edits.edit(args, client) == 0
    assert calls[0][2]["idempotency_key"] == "stable-key"
    assert "current" not in calls[0][2]
    assert "top-secret" not in capsys.readouterr().out
    document["paper_id"] = 2
    path.write_text(json.dumps(document))
    with pytest.raises(EditUsageError):
        edits.edit(args, client)
    assert len(calls) == 1


def test_login_requires_browser_approval_and_never_prints_token(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    responses = iter(
        [
            {
                "device_code": "device-secret",
                "verification_path": "/authorize-paper-edit/abc",
                "user_code": "ABC",
                "expires_in": 600,
                "interval": 5,
            },
            {"status": "authorization_pending"},
            {
                "status": "authorized",
                "paper_id": 1,
                "username": "alice",
                "access_token": "access-secret",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            },
        ]
    )
    monkeypatch.setattr(EditClient, "request", lambda *a, **kw: next(responses))
    monkeypatch.setattr(edits.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        edits.webbrowser, "open", lambda _: pytest.fail("no-browser ignored")
    )
    args = build_parser().parse_args(["auth", "login", "--paper", "1", "--no-browser"])
    assert edits.auth(args, Client()) == 0
    output = capsys.readouterr()
    assert "access-secret" not in output.out + output.err
    assert "device-secret" not in output.out + output.err
    assert CredentialStore(Client().base_url).load(1)["access_token"] == "access-secret"
