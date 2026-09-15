"""Private per-origin, per-paper credential files. Secrets never enter edit documents."""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from pwc_cli.transport import ResponseError


class CredentialStore:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.root = Path.home() / ".config" / "pwc" / "edit-credentials"

    def path(self, paper_id: int) -> Path:
        key = hashlib.sha256(f"{self.base_url}:{paper_id}".encode()).hexdigest()
        return self.root / f"{key}.json"

    def save(self, paper_id: int, credential: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ResponseError(
                "Credential directory must be owned by you with mode 0700"
            )
        fd, name = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({**credential, "base_url": self.base_url}, handle)
            os.replace(name, self.path(paper_id))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def load(self, paper_id: int) -> dict:
        path = self.path(paper_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as error:
            raise ResponseError(
                "No authorization for this paper and API. Run pwc auth login --paper PAPER"
            ) from error
        with os.fdopen(fd) as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ResponseError(
                    "Credential file must be owned by you with mode 0600"
                )
            value = json.load(handle)
        if value.get("base_url") != self.base_url or value.get("paper_id") != paper_id:
            raise ResponseError("Credential origin or paper does not match")
        return value

    def remove(self, paper_id: int) -> None:
        self.path(paper_id).unlink(missing_ok=True)
