import io
import json
import os
import stat
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ig_token  # noqa: E402

OLD, NEW = "IGAAold" + "x" * 60, "IGAAnew" + "y" * 60


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    p = tmp_path / ".ig-token"
    p.write_text(OLD + "\n")
    monkeypatch.setenv("IG_TOKEN_FILE", str(p))
    return p


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_refresh_replaces_token_privately(token_file, monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["url"] = req.full_url
        return Resp(json.dumps({"access_token": NEW, "token_type": "bearer", "expires_in": 5184000}).encode())

    monkeypatch.setattr(ig_token.urllib.request, "urlopen", fake)
    days = ig_token.refresh(now=1_000_000)
    assert round(days) == 60
    assert token_file.read_text().strip() == NEW
    assert "grant_type=ig_refresh_token" in seen["url"]
    meta = json.loads(ig_token.meta_path().read_text())
    assert meta == {"refreshed_at": 1_000_000, "expires_at": 1_000_000 + 5184000}
    if os.name == "posix":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert ig_token.days_left(now=1_000_000) == pytest.approx(60)


def test_failed_refresh_keeps_old_token_and_hides_it(token_file, monkeypatch, capsys):
    def fake(req, timeout):
        body = json.dumps({"error": {"type": "OAuthException", "code": 190, "message": "Invalid token"}})
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(ig_token.urllib.request, "urlopen", fake)
    monkeypatch.setattr(ig_token, "notify", lambda text: print("NOTIFY", text))
    monkeypatch.setattr(sys, "argv", ["ig_token.py", "refresh"])
    with pytest.raises(SystemExit) as e:
        ig_token.main()
    assert e.value.code == 1
    out = capsys.readouterr()
    assert "Invalid token" in out.err and "NOTIFY" in out.out
    assert OLD not in out.err + out.out
    assert token_file.read_text().strip() == OLD


def test_days_left_falls_back_to_file_age(token_file):
    now = token_file.stat().st_mtime + 50 * 86400
    assert ig_token.days_left(now=now) == pytest.approx(10)
