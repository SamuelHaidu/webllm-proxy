"""utils.chrome_import: conservative, extension-only import of installed-Chrome
extensions. No real Chrome is touched -- everything runs against a synthetic
`User Data` tree in tmp_path, including planted credential files that must never
be read or copied."""

import json
from pathlib import Path

import pytest

from webllm_proxy.utils import chrome_import
from webllm_proxy.utils.config import ChatgptConfig

# stealer-adjacent files that must NEVER be copied into the proxy profile
_CREDENTIAL_NAMES = {"cookies", "login data", "web data", "local state"}


def _mk_ext(ud: Path, profile: str, ext_id: str, version: str, *, theme=False, manifest=True):
    d = ud / profile / "Extensions" / ext_id / version
    d.mkdir(parents=True, exist_ok=True)
    if manifest:
        data = {"name": ext_id, "version": version.split("_", maxsplit=1)[0], "manifest_version": 3}
        if theme:
            data["theme"] = {"colors": {}}
        (d / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        (d / "background.js").write_text("// code", encoding="utf-8")
    return d


@pytest.fixture
def fake_user_data(tmp_path):
    """A realistic `User Data` dir with a Default profile, plus planted
    credential stores at the profile root that must be ignored."""
    ud = tmp_path / "User Data"
    prof = ud / "Default"
    # two normal extensions (one with two versions), a theme, and a manifest-less dir
    _mk_ext(ud, "Default", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.0.0_0")
    _mk_ext(ud, "Default", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.2.0_0")
    _mk_ext(ud, "Default", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "3.0_0")
    _mk_ext(ud, "Default", "tttttttttttttttttttttttttttttttt", "1.0_0", theme=True)
    (ud / "Default" / "Extensions" / "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn" / "1.0_0").mkdir(
        parents=True
    )
    # planted credential files (must never be touched)
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "Cookies").write_text("SECRET", encoding="utf-8")
    (prof / "Login Data").write_text("SECRET", encoding="utf-8")
    (prof / "Local State").write_text("SECRET", encoding="utf-8")
    (prof / "Network").mkdir(exist_ok=True)
    (prof / "Network" / "Cookies").write_text("SECRET", encoding="utf-8")
    return ud


def test_denylist_blocks_credential_names():
    for name in ("Login Data", "Cookies", "Local State", "Web Data", "access-token-cache"):
        assert chrome_import._denied(name)
    for name in ("manifest.json", "Extensions", "Default", "background.js"):
        assert not chrome_import._denied(name)


def test_unsafe_profile_rejected(fake_user_data):
    for bad in ("../Local State", "Network", "a/b", ".."):
        with pytest.raises(ValueError):
            chrome_import.discover_extensions(fake_user_data, bad)


def test_discover_picks_newest_skips_theme_and_invalid(fake_user_data):
    found = chrome_import.discover_extensions(fake_user_data, "Default")
    names = {p.parent.name: p.name for p in found}
    assert names == {
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": "1.2.0_0",  # newest of two versions
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": "3.0_0",
    }  # theme + manifest-less dirs excluded


def test_copy_is_idempotent_prunes_and_never_copies_credentials(fake_user_data, tmp_path):
    dest = tmp_path / "dest"
    found = chrome_import.discover_extensions(fake_user_data, "Default")

    first = chrome_import.copy_extensions(found, dest)
    assert len(first) == 2
    for p in first:
        assert (Path(p) / "manifest.json").is_file()
    # idempotent: same result, no duplication
    assert chrome_import.copy_extensions(found, dest) == first

    # no credential-named file ever landed under the destination
    copied_names = {f.name.lower() for f in dest.rglob("*")}
    assert not (copied_names & _CREDENTIAL_NAMES)

    # a newer version supersedes and prunes the old one
    _mk_ext(fake_user_data, "Default", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.3.0_0")
    found2 = chrome_import.discover_extensions(fake_user_data, "Default")
    chrome_import.copy_extensions(found2, dest)
    a_versions = {
        d.name for d in (dest / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").iterdir() if d.is_dir()
    }
    assert a_versions == {"1.3.0_0"}


def test_import_and_list_roundtrip(fake_user_data, tmp_path, monkeypatch):
    monkeypatch.setattr(chrome_import, "data_dir", lambda app: tmp_path / app)
    pc = ChatgptConfig(
        import_chrome_extensions=True,
        chrome_profile="Default",
        chrome_user_data_dir=str(fake_user_data),
    )
    imported = chrome_import.import_extensions(pc, "chatgpt")
    assert len(imported) == 2
    assert all(str(tmp_path / "chatgpt-proxy" / "imported_extensions") in p for p in imported)
    # the server-side lister returns the same set without touching real Chrome
    assert sorted(chrome_import.imported_extension_paths(pc, "chatgpt")) == sorted(imported)


def test_disabled_is_noop(fake_user_data, tmp_path, monkeypatch):
    monkeypatch.setattr(chrome_import, "data_dir", lambda app: tmp_path / app)
    pc = ChatgptConfig(import_chrome_extensions=False, chrome_user_data_dir=str(fake_user_data))
    assert chrome_import.import_extensions(pc, "chatgpt") == []
    assert chrome_import.imported_extension_paths(pc, "chatgpt") == []


def test_missing_user_data_dir_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(chrome_import, "data_dir", lambda app: tmp_path / app)
    pc = ChatgptConfig(import_chrome_extensions=True, chrome_user_data_dir=str(tmp_path / "nope"))
    assert chrome_import.import_extensions(pc, "chatgpt") == []
