"""Zhihu health checks must stay local and report unverified auth honestly."""

import json
import subprocess

import pytest

from agent_reach.backends import zhihu as backend
from agent_reach.channels import get_channel
from agent_reach.channels.zhihu import ZhihuChannel
from agent_reach.config import Config
from agent_reach.doctor import check_all, format_report


@pytest.fixture
def runtime(monkeypatch):
    binary = backend.binary_path()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake executable")
    responses = {
        ("version",): {"name": "zhihu-cli", "version": "0.6.1"},
        ("capabilities",): {
            "name": "zhihu-cli",
            "commands": [{"name": name} for name in backend.REQUIRED_COMMANDS],
        },
        ("auth", "status"): {"ok": True, "keychain": "unavailable"},
    }
    calls = []

    def run(argv, **kwargs):
        assert argv[0] == str(binary)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 10
        args = tuple(argv[1:])
        calls.append(args)
        assert args in responses, f"Doctor must not run business/update commands: {args}"
        value = responses[args]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, subprocess.CompletedProcess):
            return value
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setattr(backend.requests, "get", lambda *a, **kw: pytest.fail("no HTTP in doctor"))
    return binary, responses, calls


def test_registered():
    assert isinstance(get_channel("zhihu"), ZhihuChannel)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.zhihu.com/question/123",
        "https://www.zhihu.com/question/123/answer/456",
        "https://zhuanlan.zhihu.com/p/123",
        "https://ZHihu.com./question/123",
    ],
)
def test_urls(url):
    assert ZhihuChannel().can_handle(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://zhihu.com.evil.test/question/123",
        "https://notzhihu.com",
        "https://zhihu.com@evil.test",
        "https://user:pass@zhihu.com",
        "ftp://zhihu.com",
        "https://github.com",
    ],
)
def test_rejects_other_hosts_and_disguised_urls(url):
    assert not ZhihuChannel().can_handle(url)


def test_missing_does_not_create_files_or_probe_path(monkeypatch):
    binary = backend.binary_path()
    monkeypatch.setattr(backend.subprocess, "run", lambda *a, **kw: pytest.fail("no binary"))
    status, message = ZhihuChannel().check()
    assert status == "off"
    assert backend.INSTALL_HINT in message
    assert not binary.parent.exists()


def test_credentials_missing(runtime):
    binary, _, calls = runtime
    status, message = ZhihuChannel().check()
    assert status == "warn"
    assert str(binary) in message
    assert "auth set --secret-stdin" in message
    assert "ZHIHU_ACCESS_SECRET" in message
    assert calls == [("version",), ("capabilities",), ("auth", "status")]


@pytest.mark.parametrize("source", ["environment", "keychain"])
def test_configured_is_not_live_verified_and_never_echoes_secret(runtime, source):
    binary, responses, _ = runtime
    responses[("auth", "status")] = {
        "ok": True,
        "source": source,
        "masked": "sensitive-output",
        "verification": "not_performed",
    }
    channel = ZhihuChannel()
    channel.active_backend = "stale"
    before = binary.read_bytes()
    status, message = channel.check(Config(read_only=True))
    assert status == "warn"
    assert "已配置" in message and "不联网验证" in message
    assert "sensitive-output" not in message
    assert channel.active_backend is None
    assert binary.read_bytes() == before


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"name": "zhihu-cli", "version": "0.5.0"}, "warn"),
        ({"name": "community-zhihu", "version": "9.0.0"}, "error"),
        ({"name": "zhihu-cli", "version": "garbage"}, "error"),
        (["not an object"], "error"),
        (subprocess.CompletedProcess([], 0, "not-json", "secret"), "error"),
        (subprocess.CompletedProcess([], 1, "secret", "secret"), "error"),
        (FileNotFoundError("secret"), "error"),
        (subprocess.TimeoutExpired("secret", 10), "error"),
    ],
)
def test_bad_version_probes_are_classified_without_leaking_output(runtime, response, expected):
    _, responses, calls = runtime
    responses[("version",)] = response
    channel = ZhihuChannel()
    channel.active_backend = "stale"
    status, message = channel.check()
    assert status == expected
    assert "secret" not in message
    assert channel.active_backend is None
    assert calls == [("version",)]


def test_missing_capability(runtime):
    _, responses, calls = runtime
    responses[("capabilities",)]["commands"] = [{"name": "search zhihu"}]
    assert ZhihuChannel().check()[0] == "warn"
    assert ("auth", "status") not in calls


@pytest.mark.parametrize(
    "auth",
    [
        {"source": "keychain"},
        {"ok": False},
        {"ok": True, "source": {"invalid": True}},
        subprocess.CompletedProcess([], 7, '{"error":"private"}', "private"),
        subprocess.TimeoutExpired("private", 10),
    ],
)
def test_auth_failures_do_not_claim_ready(runtime, auth):
    _, responses, _ = runtime
    responses[("auth", "status")] = auth
    channel = ZhihuChannel()
    status, message = channel.check()
    assert status == "warn"
    assert "private" not in message
    assert channel.active_backend is None


def test_doctor_collector_includes_channel_without_mutation(runtime, monkeypatch, isolated_home):
    monkeypatch.setattr("agent_reach.doctor.get_all_channels", lambda: [ZhihuChannel()])
    before = sorted(p.relative_to(isolated_home) for p in isolated_home.rglob("*"))
    results = check_all(Config(read_only=True))
    after = sorted(p.relative_to(isolated_home) for p in isolated_home.rglob("*"))
    assert before == after
    assert results["zhihu"]["status"] == "warn"
    assert results["zhihu"]["active_backend"] is None
    assert results["zhihu"]["backends"] == ["zhihu-cli (official)"]


@pytest.mark.parametrize("configured", [False, True])
def test_doctor_text_shows_installed_zhihu_diagnostics(runtime, monkeypatch, configured):
    _, responses, _ = runtime
    if configured:
        responses[("auth", "status")] = {"ok": True, "source": "keychain"}
    monkeypatch.setattr("agent_reach.doctor.get_all_channels", lambda: [ZhihuChannel()])
    report = format_report(check_all(Config(read_only=True)))
    assert "知乎搜索、热榜和回答摘要" in report
    assert ("Access Secret 已配置" if configured else "auth set --secret-stdin") in report
    assert "0/1" in report  # A local credential is not proof of live availability.
    assert "可以解锁" not in report


def test_doctor_text_shows_broken_zhihu_instead_of_install_summary(runtime, monkeypatch):
    _, responses, _ = runtime
    responses[("version",)] = {"name": "zhihu-cli", "version": "invalid"}
    monkeypatch.setattr("agent_reach.doctor.get_all_channels", lambda: [ZhihuChannel()])
    results = check_all(Config(read_only=True))
    assert results["zhihu"]["status"] == "error"
    report = format_report(results)
    assert results["zhihu"]["message"] in report
    assert "可以解锁" not in report


def test_doctor_text_keeps_absent_zhihu_in_install_summary(monkeypatch):
    monkeypatch.setattr("agent_reach.doctor.get_all_channels", lambda: [ZhihuChannel()])
    report = format_report(check_all(Config(read_only=True)))
    assert "还有 1 个可选渠道可以解锁（知乎搜索、热榜和回答摘要）" in report
