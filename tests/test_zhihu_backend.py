"""Official binary discovery and installation; all downloads/probes are mocked."""

import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile

import pytest

from agent_reach.backends import zhihu


@pytest.mark.parametrize(
    ("system", "suffix"),
    [
        ("Windows", "AppData/Local/ZhihuCLI/current/zhihu-cli.exe"),
        ("Darwin", "Library/Application Support/zhihu-cli/current/zhihu-cli"),
        ("Linux", ".local/share/zhihu-cli/current/zhihu-cli"),
    ],
)
def test_official_paths(monkeypatch, isolated_home, system, suffix):
    monkeypatch.setattr(zhihu.platform, "system", lambda: system)
    assert zhihu.binary_path() == isolated_home / suffix
    assert not zhihu.binary_path().exists()


def test_explicit_home_and_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(zhihu.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert zhihu.binary_path() == tmp_path / "xdg/zhihu-cli/current/zhihu-cli"
    monkeypatch.setenv("ZHIHU_CLI_HOME", str(tmp_path / "custom"))
    assert zhihu.binary_path() == tmp_path / "custom/current/zhihu-cli"
    monkeypatch.setenv("ZHIHU_CLI_HOME", "relative")
    with pytest.raises(zhihu.ZhihuCLIError, match="绝对路径"):
        zhihu.binary_path()


@pytest.mark.parametrize(
    ("system", "machine", "key"),
    [
        ("Windows", "AMD64", "windows-amd64"),
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-amd64"),
        ("Linux", "x86_64", "linux-amd64"),
        ("Linux", "aarch64", "linux-arm64"),
    ],
)
def test_release_platform(monkeypatch, system, machine, key):
    monkeypatch.setattr(zhihu.platform, "system", lambda: system)
    monkeypatch.setattr(zhihu.platform, "machine", lambda: machine)
    assert zhihu._platform_key() == key


def test_unsupported_platform_does_not_download(monkeypatch):
    monkeypatch.setattr(zhihu.platform, "machine", lambda: "unsupported")
    monkeypatch.setattr(zhihu, "_download", lambda *a: pytest.fail("must not download"))
    with pytest.raises(zhihu.ZhihuCLIError, match="不支持"):
        zhihu.install_cli()


def archive_bytes(name, payload=b"new official binary", *, link=False, duplicate=False):
    buffer = io.BytesIO()
    if name.endswith(".exe"):
        with zipfile.ZipFile(buffer, "w") as package:
            entry = zipfile.ZipInfo(name)
            if link:
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(entry, payload)
            if duplicate:
                package.writestr("other/" + name, payload)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as package:
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            if link:
                entry.type = tarfile.SYMTYPE
                entry.linkname = "outside"
            package.addfile(entry, io.BytesIO(payload))
            if duplicate:
                entry = tarfile.TarInfo("other/" + name)
                entry.size = len(payload)
                package.addfile(entry, io.BytesIO(payload))
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["zhihu-cli.exe", "zhihu-cli"])
def test_extracts_regular_binary(name):
    assert zhihu._extract_binary(archive_bytes(name), name) == b"new official binary"


@pytest.mark.parametrize("name", ["zhihu-cli.exe", "zhihu-cli"])
@pytest.mark.parametrize("kind", ["traversal", "absolute", "link", "duplicate", "missing"])
def test_rejects_unsafe_or_ambiguous_archives(name, kind):
    member = {"traversal": "../" + name, "absolute": "/" + name, "missing": "other-" + name}.get(
        kind, name
    )
    data = archive_bytes(member, link=kind == "link", duplicate=kind == "duplicate")
    with pytest.raises(zhihu.ZhihuCLIError):
        zhihu._extract_binary(data, name)


@pytest.fixture
def release(monkeypatch):
    target = zhihu.binary_path()
    archive = archive_bytes(target.name)
    key = "windows-amd64" if target.suffix == ".exe" else "linux-amd64"
    manifest = {
        "schema_version": 1,
        "cli": {
            "latest_version": "0.6.1",
            "artifacts": {
                key: {
                    "url": "https://developer-cdn.zhihu.com/zhihu-cli/releases/stable/cli/test",
                    "sha256": hashlib.sha256(archive).hexdigest(),
                    "size": len(archive),
                }
            },
        },
    }
    calls = []

    def download(url, limit):
        calls.append(url)
        return json.dumps(manifest).encode() if url == zhihu.MANIFEST_URL else archive

    monkeypatch.setattr(zhihu, "_platform_key", lambda: key)
    monkeypatch.setattr(zhihu, "_download", download)
    monkeypatch.setattr(zhihu, "inspect_binary", lambda path: "0.6.1")
    return target, manifest, calls, key


def test_installs_verified_binary_in_official_layout(release):
    target, _, calls, _ = release
    assert zhihu.install_cli() == target
    assert target.read_bytes() == b"new official binary"
    assert (
        target.parent.parent / "versions/0.6.1" / target.name
    ).read_bytes() == target.read_bytes()
    assert len(calls) == 2
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_install_accepts_system_temp_directory_symlink(release, monkeypatch, tmp_path):
    """macOS commonly exposes its temporary directory through /var -> /private/var."""
    real_temp = tmp_path / "real-temp"
    real_temp.mkdir()
    alias = tmp_path / "linked-temp"
    try:
        alias.symlink_to(real_temp, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires privileges on this host")
    monkeypatch.setattr(zhihu.tempfile, "tempdir", str(alias))

    target, _, _, _ = release
    assert zhihu.install_cli() == target
    assert target.read_bytes() == b"new official binary"
    assert list(real_temp.iterdir()) == []


def test_reuses_compatible_install_without_download(release):
    target, _, calls, _ = release
    target.parent.mkdir(parents=True)
    target.write_bytes(b"already installed")
    assert zhihu.install_cli() == target
    assert target.read_bytes() == b"already installed"
    assert calls == []


@pytest.mark.parametrize("failure", ["hash", "size", "schema", "platform", "version", "binary"])
def test_failed_repair_preserves_existing_binary(release, monkeypatch, failure):
    target, manifest, _, key = release
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old binary")

    def inspect(path):
        if path == target:
            raise zhihu.ZhihuCLIError("needs repair")
        return "0.6.2" if failure == "binary" else "0.6.1"

    monkeypatch.setattr(zhihu, "inspect_binary", inspect)
    artifact = manifest["cli"]["artifacts"][key]
    if failure == "hash":
        artifact["sha256"] = "0" * 64
    elif failure == "size":
        artifact["size"] += 1
    elif failure == "schema":
        manifest["schema_version"] = 2
    elif failure == "platform":
        manifest["cli"]["artifacts"] = {}
    elif failure == "version":
        manifest["cli"]["latest_version"] = "0.5.0"
    with pytest.raises(zhihu.ZhihuCLIError):
        zhihu.install_cli()
    assert target.read_bytes() == b"old binary"


@pytest.mark.parametrize(
    "url",
    [
        "http://developer-cdn.zhihu.com/zhihu-cli/releases/a",
        "https://evil.test/zhihu-cli/releases/a",
        "https://developer-cdn.zhihu.com.evil.test/zhihu-cli/releases/a",
        "https://user:secret@developer-cdn.zhihu.com/zhihu-cli/releases/a",
        "https://developer-cdn.zhihu.com/unrelated/a",
    ],
)
def test_download_rejects_non_official_urls(monkeypatch, url):
    monkeypatch.setattr(zhihu.requests, "get", lambda *a, **kw: pytest.fail("must not connect"))
    with pytest.raises(zhihu.ZhihuCLIError):
        zhihu._download(url, 100)


@pytest.mark.parametrize(
    ("status", "body", "limit", "success"),
    [
        (200, b"abc", 3, True),
        (302, b"", 3, False),
        (200, b"abcd", 3, False),
    ],
)
def test_download_is_bounded_and_never_follows_redirects(monkeypatch, status, body, limit, success):
    class Response:
        status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, chunk_size):
            yield body

    def get(url, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        return Response()

    monkeypatch.setattr(zhihu.requests, "get", get)
    if success:
        assert zhihu._download(zhihu.MANIFEST_URL, limit) == body
    else:
        with pytest.raises(zhihu.ZhihuCLIError):
            zhihu._download(zhihu.MANIFEST_URL, limit)


def test_atomic_write_failure_preserves_old_binary(monkeypatch, tmp_path):
    path = tmp_path / "current/zhihu-cli"
    path.parent.mkdir()
    path.write_bytes(b"old")

    def fail(*args):
        raise OSError("replacement failed")

    monkeypatch.setattr(zhihu.os, "replace", fail)
    with pytest.raises(OSError):
        zhihu._write_executable(path, b"new")
    assert path.read_bytes() == b"old"
    assert list(path.parent.iterdir()) == [path]
