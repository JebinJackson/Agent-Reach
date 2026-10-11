"""Zhihu follows the existing opt-in installer and packaged Skill contracts."""

from argparse import Namespace
from pathlib import Path

import pytest

from agent_reach import cli
from agent_reach.backends import zhihu


def test_installer_reuses_backend_and_prints_absolute_path(monkeypatch, capsys, tmp_path):
    binary = tmp_path / "ZhihuCLI/current/zhihu-cli.exe"
    monkeypatch.setattr(zhihu, "install_cli", lambda: binary)
    assert cli._install_zhihu_deps() is True
    output = capsys.readouterr().out
    assert str(binary) in output
    assert "auth set --secret-stdin" in output


def test_install_failure_is_reported_without_upstream_output(monkeypatch, capsys):
    def fail():
        raise OSError("private upstream output")

    monkeypatch.setattr(zhihu, "install_cli", fail)
    assert cli._install_zhihu_deps() is False
    output = capsys.readouterr().out
    assert "private upstream output" not in output
    assert zhihu.DOCS_URL in output


@pytest.fixture
def installers(monkeypatch):
    calls = []
    for name in (
        "_install_system_deps",
        "_install_mcporter",
        "_install_skill",
        "_install_system_deps_safe",
        "_install_system_deps_dryrun",
        "_install_mcporter_safe",
        "_install_twitter_deps",
        "_install_xiaoyuzhou_deps",
        "_install_xhs_deps",
        "_install_reddit_deps",
        "_install_opencli_deps",
        "_install_bili_deps",
        "_install_boss_deps",
    ):
        monkeypatch.setattr(cli, name, lambda: True)
    monkeypatch.setattr(cli, "_install_zhihu_deps", lambda: calls.append("zhihu") or True)
    monkeypatch.setattr("agent_reach.doctor.check_all", lambda config: {})
    monkeypatch.setattr("agent_reach.doctor.format_report", lambda results: "mock doctor")
    return calls


@pytest.mark.parametrize("env", ["local", "server"])
@pytest.mark.parametrize("channels", ["zhihu", "zhihu,zhihu", "all"])
def test_explicit_channel_and_all_install_once(installers, env, channels):
    cli._cmd_install(
        Namespace(
            env=env,
            proxy="",
            system=True,
            safe=False,
            dry_run=False,
            channels=channels,
        )
    )
    assert installers == ["zhihu"]


@pytest.mark.parametrize(
    ("system", "safe", "dry_run", "channels"),
    [
        (False, False, False, "zhihu"),
        (False, True, False, "zhihu"),
        (True, False, True, "zhihu"),
        (True, False, False, ""),
    ],
)
def test_check_only_and_unrequested_channels_never_install(
    installers, system, safe, dry_run, channels
):
    cli._cmd_install(
        Namespace(
            env="local",
            proxy="",
            system=system,
            safe=safe,
            dry_run=dry_run,
            channels=channels,
        )
    )
    assert installers == []


def test_failed_requested_install_sets_failure_exit(installers, monkeypatch):
    monkeypatch.setattr(cli, "_install_zhihu_deps", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli._cmd_install(
            Namespace(
                env="server",
                proxy="",
                system=True,
                safe=False,
                dry_run=False,
                channels="zhihu",
            )
        )
    assert exc.value.code == 1


def test_cli_accepts_zhihu_in_dry_run(installers, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["agent-reach", "install", "--channels=zhihu", "--dry-run"])
    monkeypatch.setattr(cli, "_detect_environment", lambda: "local")
    cli.main()
    assert installers == []
    assert "Would install optional channels: zhihu" in capsys.readouterr().out


def test_skill_install_copies_zhihu_guide(isolated_home, monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert cli._install_skill()
    target = isolated_home / ".agents/skills/agent-reach"
    assert "references/zhihu.md" in (target / "SKILL.md").read_text(encoding="utf-8")
    guide = (target / "references/zhihu.md").read_text(encoding="utf-8")
    assert "search zhihu --query" in guide
    assert "auth set --secret-stdin" in guide
    assert "Paging.NextOffset" in guide


def test_zhihu_docs_do_not_install_a_community_package():
    root = Path(__file__).resolve().parents[1]
    guide = (root / "agent_reach/skill/references/zhihu.md").read_text(encoding="utf-8")
    assert "pip install zhihu" not in guide
    assert "npm install" not in guide
    assert "auth set --secret-stdin" in guide
    assert "not full text" in guide
