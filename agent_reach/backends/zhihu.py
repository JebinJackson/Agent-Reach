"""Locate, inspect and install the official Zhihu CLI (not community `zhihu` tools).

Content requests remain the agent's responsibility. Local probes never verify
credentials online, query content, or run the CLI's update-checking `status`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import requests

from agent_reach.utils.paths import ensure_no_symlink_path, home_dir, make_private_dir
from agent_reach.utils.process import utf8_subprocess_env

MIN_VERSION = "0.6.1"
MANIFEST_URL = "https://developer-cdn.zhihu.com/zhihu-cli/releases/stable/manifest.json"
DOCS_URL = "https://developer.zhihu.com/docs?key=zhihu_cli"
INSTALL_HINT = "agent-reach install --system --channels=zhihu"
REQUIRED_COMMANDS = {"search zhihu", "hot", "question answers"}
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024


class ZhihuCLIError(RuntimeError):
    """A diagnostic that contains no upstream output or credentials."""


class ZhihuCLIIncompatible(ZhihuCLIError):
    """An executable installation needs an explicit upgrade or repair."""


def cli_home() -> Path:
    """Use the same data directories as the official installer; never create them."""
    override = os.environ.get("ZHIHU_CLI_HOME")
    if override:
        root = Path(override)
    elif platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA") or home_dir() / "AppData" / "Local") / "ZhihuCLI"
    elif platform.system() == "Darwin":
        root = home_dir() / "Library" / "Application Support" / "zhihu-cli"
    elif platform.system() == "Linux":
        root = (
            Path(os.environ.get("XDG_DATA_HOME") or home_dir() / ".local" / "share") / "zhihu-cli"
        )
    else:
        raise ZhihuCLIError("知乎官方 CLI 不支持当前操作系统")
    if not root.is_absolute():
        raise ZhihuCLIError("ZHIHU_CLI_HOME / 用户数据目录必须是绝对路径")
    return root


def binary_path() -> Path:
    """Do not trust a same-named executable on PATH or a community `zhihu` command."""
    name = "zhihu-cli.exe" if platform.system() == "Windows" else "zhihu-cli"
    return cli_home() / "current" / name


def _version(value: object) -> tuple[int, int, int]:
    # Official stable releases use major.minor.patch. Reject prereleases and
    # malformed versions instead of mistakenly treating them as compatible.
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ZhihuCLIError("知乎 CLI 返回了无效的稳定版本号")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def run_json(binary: Path, *args: str) -> dict:
    """Run a local, non-interactive probe with a bounded wait and sanitized errors."""
    try:
        result = subprocess.run(
            [str(binary), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=utf8_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ZhihuCLIError("知乎 CLI 本地检查超时；未验证认证状态") from exc
    except OSError as exc:
        raise ZhihuCLIError(f"知乎 CLI 无法执行；修复：{INSTALL_HINT}") from exc
    if result.returncode != 0:
        # Do not forward even masked credentials or arbitrary stdout/stderr.
        raise ZhihuCLIError(f"知乎 CLI 本地检查失败（退出码 {result.returncode}）")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ZhihuCLIError("知乎 CLI 未返回合法 JSON") from exc
    if not isinstance(data, dict) or data.get("ok") is False:
        raise ZhihuCLIError("知乎 CLI 返回了异常状态")
    return data


def inspect_binary(binary: Path) -> str:
    """Check the official identity, minimum version and the documented capabilities."""
    info = run_json(binary, "version")
    if info.get("name") != "zhihu-cli":
        raise ZhihuCLIError("检测到的程序不是知乎官方 zhihu-cli")
    version = info.get("version")
    if _version(version) < _version(MIN_VERSION):
        raise ZhihuCLIIncompatible(f"知乎 CLI 版本过旧，需要 >= {MIN_VERSION}；运行 {INSTALL_HINT}")
    capabilities = run_json(binary, "capabilities")
    commands = capabilities.get("commands")
    if capabilities.get("name") != "zhihu-cli" or not isinstance(commands, list):
        raise ZhihuCLIError("知乎 CLI 能力清单无效")
    names = {
        item.get("name")
        for item in commands
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if not REQUIRED_COMMANDS <= names:
        raise ZhihuCLIIncompatible("知乎 CLI 缺少搜索、热榜或回答摘要能力；请修复安装")
    return str(version)


def _platform_key() -> str:
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower()
    )
    key = f"{system}-{arch}"
    if key not in {"windows-amd64", "darwin-amd64", "darwin-arm64", "linux-amd64", "linux-arm64"}:
        raise ZhihuCLIError("知乎官方 CLI 暂不支持当前系统/架构")
    return key


def _download(url: str, limit: int) -> bytes:
    """Only fetch official HTTPS artifacts, without redirects, and bound the body."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "developer-cdn.zhihu.com"
        or not parsed.path.startswith("/zhihu-cli/releases/")
        or parsed.fragment
    ):
        raise ZhihuCLIError("知乎 CLI 下载地址不是官方发布地址")
    with requests.get(url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
        if response.status_code != 200:
            raise ZhihuCLIError("知乎 CLI 下载失败（重定向不被接受）")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise ZhihuCLIError("知乎 CLI 下载超过大小限制")
        return bytes(data)


def _extract_binary(archive: bytes, name: str) -> bytes:
    """Read one regular executable; never extract archive paths to disk."""

    def valid_path(value: str) -> bool:
        path = PurePosixPath(value)
        return (
            not path.is_absolute()
            and ".." not in path.parts
            and "\\" not in value
            and ":" not in value
        )

    if name.endswith(".exe"):
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            entries = package.infolist()
            if any(
                not valid_path(e.filename) or stat.S_ISLNK(e.external_attr >> 16) for e in entries
            ):
                raise ZhihuCLIError("知乎 CLI 压缩包含不安全路径或链接")
            matches = [
                e for e in entries if PurePosixPath(e.filename).name == name and not e.is_dir()
            ]
            if len(matches) != 1 or not 0 < matches[0].file_size <= MAX_DOWNLOAD_BYTES:
                raise ZhihuCLIError("知乎 CLI 压缩包缺少唯一有效的可执行文件")
            with package.open(matches[0]) as handle:
                data = handle.read(MAX_DOWNLOAD_BYTES + 1)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar_package:
            members = tar_package.getmembers()
            if any(not valid_path(e.name) or not (e.isfile() or e.isdir()) for e in members):
                raise ZhihuCLIError("知乎 CLI 压缩包含不安全路径或链接")
            tar_matches = [e for e in members if PurePosixPath(e.name).name == name and e.isfile()]
            if len(tar_matches) != 1 or not 0 < tar_matches[0].size <= MAX_DOWNLOAD_BYTES:
                raise ZhihuCLIError("知乎 CLI 压缩包缺少唯一有效的可执行文件")
            tar_handle = tar_package.extractfile(tar_matches[0])
            if tar_handle is None:
                raise ZhihuCLIError("无法读取知乎 CLI 可执行文件")
            with tar_handle:
                data = tar_handle.read(MAX_DOWNLOAD_BYTES + 1)
    if not 0 < len(data) <= MAX_DOWNLOAD_BYTES:
        raise ZhihuCLIError("知乎 CLI 可执行文件大小异常")
    return data


def _write_executable(path: Path, payload: bytes) -> None:
    """Stage beside the target and replace atomically, preserving a previous binary on error."""
    ensure_no_symlink_path(path, "知乎 CLI 路径")
    make_private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".zhihu-cli-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o700)
        ensure_no_symlink_path(path, "知乎 CLI 路径")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_cli() -> Path:
    """Explicit-install-only entry point; never touch credentials, PATH or agent skills."""
    key = _platform_key()
    target = binary_path()
    ensure_no_symlink_path(target, "知乎 CLI 路径")
    if target.is_file():
        try:
            inspect_binary(target)
            return target
        except ZhihuCLIError:
            pass  # Repair an incompatible or broken installation only after staging.

    manifest = json.loads(_download(MANIFEST_URL, 1024 * 1024))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ZhihuCLIError("知乎 CLI 发布清单格式不兼容")
    release = manifest.get("cli")
    if not isinstance(release, dict) or _version(release.get("latest_version")) < _version(
        MIN_VERSION
    ):
        raise ZhihuCLIError("知乎 CLI 发布版本不兼容")
    version = release["latest_version"]
    artifacts = release.get("artifacts")
    artifact = artifacts.get(key) if isinstance(artifacts, dict) else None
    if not isinstance(artifact, dict):
        raise ZhihuCLIError("知乎 CLI 发布清单缺少当前平台")
    url, checksum, size = artifact.get("url"), artifact.get("sha256"), artifact.get("size")
    if (
        not isinstance(url, str)
        or not isinstance(checksum, str)
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        or type(size) is not int
        or not 0 < size <= MAX_DOWNLOAD_BYTES
    ):
        raise ZhihuCLIError("知乎 CLI 发布清单校验字段无效")
    archive = _download(url, size)
    if len(archive) != size or hashlib.sha256(archive).hexdigest() != checksum:
        raise ZhihuCLIError("知乎 CLI 下载大小或 SHA-256 校验失败")
    payload = _extract_binary(archive, target.name)
    with tempfile.TemporaryDirectory(prefix="agent-reach-zhihu-") as directory:
        # Canonicalize only our newly created staging directory: system temp
        # roots may use symlinks (e.g. /var on macOS). Keep install-path checks.
        staged = Path(directory).resolve(strict=True) / target.name
        _write_executable(staged, payload)
        if inspect_binary(staged) != version:
            raise ZhihuCLIError("知乎 CLI 二进制版本与发布清单不一致")
        _write_executable(cli_home() / "versions" / version / target.name, payload)
        _write_executable(target, payload)
    return target
