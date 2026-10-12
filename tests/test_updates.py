"""Finding out about a newer CAVY, and refusing to mix versions on one server."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform import updates
from eaal_platform.api.bridge import CavyApi
from eaal_platform.buildinfo import VERSION, VERSION_HEADER
from eaal_platform.client.api import ClientApi
from eaal_platform.client.remote import RemoteBackend, VersionMismatchError
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, create_app

_BASE = f"https://github.com/{updates.REPO}"


def _release(tag: str, *, draft: bool = False, assets: list[str] | None = None) -> dict[str, Any]:
    names = assets if assets is not None else [f"CAVY-{tag.lstrip('v')}-Windows-Setup.exe"]
    return {
        "tag_name": tag,
        "draft": draft,
        "html_url": f"{_BASE}/releases/tag/{tag}",
        "body": f"Notes for {tag}",
        "assets": [
            {"name": n, "browser_download_url": f"{_BASE}/releases/download/{tag}/{n}"}
            for n in names
        ],
    }


class _GitHub:
    """A stand-in for GitHub: serves a list of releases and counts the questions."""

    def __init__(self, releases: list[dict[str, Any]] | None = None, down: bool = False) -> None:
        self.releases = releases or []
        self.down = down
        self.calls = 0

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            if self.down:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, json=self.releases)

        return httpx.Client(transport=httpx.MockTransport(handler))


def _check(
    github: _GitHub, tmp_path: Path, current: str = "0.2.0", **kwargs: Any
) -> dict[str, Any]:
    return updates.check_for_update(
        current, http=github.client(), state_file=tmp_path / "state.json", **kwargs
    )


# -- version numbers -----------------------------------------------------------------


def test_versions_are_read_and_compared_as_numbers() -> None:
    assert updates.parse_version("v0.2.10") == (0, 2, 10)
    assert updates.parse_version("1.0.0") == (1, 0, 0)
    for bad in ("", None, "dev", "0.2", "0.2.0-rc1", "v1.x.0"):
        assert updates.parse_version(bad) is None
    assert updates.is_newer("0.10.0", "0.9.9")  # not a text comparison
    assert not updates.is_newer("0.2.0", "0.2.0")
    assert not updates.is_newer("0.1.9", "0.2.0")
    assert not updates.is_newer("garbage", "0.2.0")


def test_the_version_is_written_in_one_place() -> None:
    root = Path(__file__).resolve().parent.parent
    declared = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert declared == VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)
    assert "VERSION = " not in (root / "scripts" / "build_release.py").read_text().replace(
        "from eaal_platform.buildinfo import VERSION", ""
    ).replace("{VERSION}", "")


# -- the check -------------------------------------------------------------------------


def test_a_newer_release_is_reported_with_the_right_download(tmp_path: Path) -> None:
    github = _GitHub([_release("v0.1.0"), _release("v0.3.0"), _release("v0.2.5")])

    result = _check(github, tmp_path)

    assert result["ok"] and result["update_available"] and not result["snoozed"]
    assert (result["current"], result["latest"]) == ("0.2.0", "0.3.0")
    assert result["page"] == f"{_BASE}/releases/tag/v0.3.0"
    assert result["notes"] == "Notes for v0.3.0"


def test_nothing_is_reported_when_up_to_date_or_ahead(tmp_path: Path) -> None:
    for latest in ("v0.2.0", "v0.1.0"):
        result = _check(_GitHub([_release(latest)]), tmp_path / latest)
        assert result["ok"] and result["update_available"] is False


def test_drafts_and_odd_tags_are_ignored(tmp_path: Path) -> None:
    github = _GitHub([_release("v9.0.0", draft=True), _release("nightly"), _release("v0.2.1")])

    assert _check(github, tmp_path)["latest"] == "0.2.1"


def test_no_releases_at_all_means_no_update(tmp_path: Path) -> None:
    result = _check(_GitHub([]), tmp_path)

    assert result["ok"] and result["update_available"] is False


def test_being_offline_is_quiet_not_an_error(tmp_path: Path) -> None:
    result = _check(_GitHub(down=True), tmp_path)

    assert result == {
        "ok": False,
        "current": "0.2.0",
        "error": "Couldn't reach GitHub to check for updates.",
    }


def test_the_answer_is_kept_for_half_a_day(tmp_path: Path) -> None:
    github = _GitHub([_release("v0.3.0")])
    clock = [1000.0]

    def now() -> float:
        return clock[0]

    first = _check(github, tmp_path, now=now)
    clock[0] += 3600
    again = _check(github, tmp_path, now=now)
    assert github.calls == 1 and again["latest"] == first["latest"]

    github.releases = [_release("v0.4.0")]
    assert _check(github, tmp_path, now=now)["latest"] == "0.3.0"  # still the kept answer
    assert _check(github, tmp_path, now=now, force=True)["latest"] == "0.4.0"
    clock[0] += 13 * 3600
    assert _check(github, tmp_path, now=now)["latest"] == "0.4.0"


def test_a_kept_answer_still_works_when_offline_later(tmp_path: Path) -> None:
    clock = [0.0]
    _check(_GitHub([_release("v0.3.0")]), tmp_path, now=lambda: clock[0])
    clock[0] += 20 * 3600

    later = _check(_GitHub(down=True), tmp_path, now=lambda: clock[0])

    assert later["ok"] and later["update_available"]


def test_remind_me_later_hides_the_notice_until_the_next_version(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    github = _GitHub([_release("v0.3.0")])
    clock = [0.0]

    def now() -> float:
        return clock[0]

    def check() -> dict[str, Any]:
        return updates.check_for_update("0.2.0", http=github.client(), state_file=state, now=now)

    assert check()["snoozed"] is False
    updates.snooze("0.3.0", now=now, state_file=state)
    assert check()["snoozed"] is True
    clock[0] += 4 * 24 * 3600
    assert check()["snoozed"] is False  # three days passed
    updates.snooze("0.3.0", now=now, state_file=state)
    github.releases = [_release("v0.3.1")]
    clock[0] += 13 * 3600
    assert check()["snoozed"] is False  # a newer version brings the notice back


def test_a_corrupt_state_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{not json")

    assert _check(_GitHub([_release("v0.3.0")]), tmp_path)["update_available"]


def test_only_this_projects_own_downloads_are_offered() -> None:
    assets = [
        {
            "name": "CAVY-0.3.0-Windows-Setup.exe",
            "browser_download_url": "https://evil.example/x.exe",
        },
        {
            "name": "CAVY-0.3.0-Windows-Setup.exe",
            "browser_download_url": f"{_BASE}/releases/download/v0.3.0/ok.exe",
        },
    ]

    assert updates.pick_asset(assets, system="win32") == f"{_BASE}/releases/download/v0.3.0/ok.exe"
    assert updates.pick_asset(assets[:1], system="win32") is None


def test_the_download_matches_the_computer() -> None:
    names = [
        "CAVY-0.3.0-Windows-Setup.exe",
        "CAVY-0.3.0-macOS-AppleSilicon.dmg",
        "CAVY-0.3.0-macOS-Intel.dmg",
    ]
    assets = _release("v0.3.0", assets=names)["assets"]

    assert updates.pick_asset(assets, "win32", "AMD64").endswith("Windows-Setup.exe")
    assert updates.pick_asset(assets, "darwin", "arm64").endswith("AppleSilicon.dmg")
    assert updates.pick_asset(assets, "darwin", "x86_64").endswith("Intel.dmg")
    assert updates.pick_asset(assets, "linux", "x86_64") is None


def test_a_release_page_from_elsewhere_is_replaced_by_ours(tmp_path: Path) -> None:
    odd = _release("v0.3.0")
    odd["html_url"] = "https://evil.example/download"

    assert _check(_GitHub([odd]), tmp_path)["page"] == updates.RELEASES_PAGE


def test_mismatch_messages_say_which_side_to_update() -> None:
    older_app = updates.describe_mismatch("0.2.0", "0.3.0")
    assert "Update this app to 0.3.0" in older_app
    older_server = updates.describe_mismatch("0.3.0", "0.2.0")
    assert "server needs to be updated to 0.3.0" in older_server
    assert "older CAVY" in updates.describe_mismatch("0.3.0", None)
    assert "same version" in older_app and "same version" in older_server


# -- the server refuses a different version ---------------------------------------------


@pytest.fixture
def raw_server(
    db_engine: Engine, db_session_factory: sessionmaker[OrmSession]
) -> Iterator[TestClient]:
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:  # note: no version header by default
        yield http


def test_the_server_reports_its_version(raw_server: TestClient) -> None:
    assert raw_server.get("/api/health").json()["app_version"] == VERSION


@pytest.mark.parametrize("theirs", [None, "0.0.1", "99.0.0", "garbage"])
def test_calls_from_a_different_version_are_refused(
    raw_server: TestClient, theirs: str | None
) -> None:
    headers = {VERSION_HEADER: theirs} if theirs else {}

    for response in (
        raw_server.post(
            "/api/login",
            json={"role": "student", "email": "a@b.c", "password": "x"},
            headers=headers,
        ),
        raw_server.post("/api/call/get_labs", json={"args": []}, headers=headers),
        raw_server.get("/api/academic", headers=headers),
    ):
        assert response.status_code == 426
        body = response.json()
        assert body["code"] == "version_mismatch" and body["server_version"] == VERSION
        assert "same version" in body["error"]


def test_the_same_version_is_let_through(raw_server: TestClient) -> None:
    response = raw_server.get("/api/academic", headers={VERSION_HEADER: VERSION})

    assert response.status_code == 200


def test_health_and_the_admin_panel_do_not_need_the_header(raw_server: TestClient) -> None:
    assert raw_server.get("/api/health").status_code == 200
    assert raw_server.get("/admin").status_code == 200  # a browser, not the app


def test_the_app_stops_with_a_clear_message_when_versions_differ(raw_server: TestClient) -> None:
    other = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                426, json={"error": "This server runs CAVY 9.9.9, but this app is 0.2.0. Update."}
            )
        )
    )
    backend = RemoteBackend("http://server", http=other)

    with pytest.raises(VersionMismatchError, match=r"9\.9\.9"):
        backend.login("student", "a@b.c", "x")
    with pytest.raises(VersionMismatchError):
        backend.call("get_labs")


def test_every_request_from_the_app_carries_its_version() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get(VERSION_HEADER))
        return httpx.Response(200, json={"service": "cavy", "app_version": VERSION})

    backend = RemoteBackend(
        "http://server", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    backend.health()

    assert seen == [VERSION]


# -- the app: connecting, version info, hosting -------------------------------------------


def _app(db_session_factory: sessionmaker[OrmSession]) -> ClientApi:
    return ClientApi(CavyApi(db_session_factory, EventLogger(db_session_factory)))


def _fake_server(monkeypatch: pytest.MonkeyPatch, app_version: str | None) -> None:
    def health(self: RemoteBackend) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": True, "service": "cavy", "version": 1}
        if app_version is not None:
            body["app_version"] = app_version
        return body

    monkeypatch.setattr(RemoteBackend, "health", health)


def test_the_app_will_not_connect_to_a_server_on_another_version(
    db_session_factory: sessionmaker[OrmSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(db_session_factory)
    for theirs in ("0.1.0", "9.0.0", None):
        _fake_server(monkeypatch, theirs)
        result = app.set_server("10.0.0.5:8000")
        assert result["ok"] is False and result["version_mismatch"] is True
        assert "same version" in result["error"]
        assert app.get_server_settings()["mode"] == "local"


def test_the_app_connects_to_a_server_on_the_same_version(
    db_session_factory: sessionmaker[OrmSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EAAL_DB_PATH", str(tmp_path / "x.db"))
    _fake_server(monkeypatch, VERSION)
    app = _app(db_session_factory)

    result = app.set_server("10.0.0.5:8000")

    assert result["ok"] is True and app.get_server_settings()["mode"] == "server"


def test_version_info_tells_whether_the_server_matches(
    db_session_factory: sessionmaker[OrmSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EAAL_DB_PATH", str(tmp_path / "x.db"))
    app = _app(db_session_factory)
    assert app.get_version_info()["server"] is None and app.get_version_info()["app"] == VERSION

    _fake_server(monkeypatch, VERSION)
    app.set_server("10.0.0.5:8000")
    assert app.get_version_info()["server"]["compatible"] is True

    _fake_server(monkeypatch, "9.0.0")  # the server is updated later
    info = app.get_version_info()["server"]
    assert info["compatible"] is False and "Update this app to 9.0.0" in info["message"]


def test_checking_and_postponing_go_through_to_the_update_module(
    db_session_factory: sessionmaker[OrmSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda current, force=False: calls.append((current, force)) or {"ok": True},
    )
    monkeypatch.setattr(updates, "snooze", lambda version: calls.append(("snooze", version)))
    app = _app(db_session_factory)

    assert app.check_for_update(True) == {"ok": True}
    assert app.snooze_update("0.3.0") == {"ok": True}
    assert calls == [(VERSION, True), ("snooze", "0.3.0")]


def test_the_update_check_file_lives_in_the_data_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EAAL_DB_PATH", str(tmp_path / "data" / "cavy.db"))

    updates.snooze("0.3.0")

    saved = json.loads((tmp_path / "data" / "update_check.json").read_text())
    assert saved["snoozed"]["version"] == "0.3.0"


def test_the_sign_up_lists_also_refuse_a_different_server_version() -> None:
    other = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                426, json={"error": "Update this app. Same version needed."}
            )
        )
    )
    backend = RemoteBackend("http://server", http=other)

    with pytest.raises(VersionMismatchError):
        backend.get_academic_options()
    with pytest.raises(VersionMismatchError):
        backend.get_signup_info()


def test_mail_trusts_the_bundled_certificate_authorities(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import ssl

    from eaal_platform import mailer

    context = mailer._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 0  # the certifi list, not an empty store

    real_import = builtins.__import__

    def no_certifi(name: str, *args: object, **kwargs: object) -> object:
        if name == "certifi":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    assert mailer._tls_context().verify_mode == ssl.CERT_REQUIRED  # falls back, still verifying
