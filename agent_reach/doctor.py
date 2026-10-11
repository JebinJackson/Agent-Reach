# -*- coding: utf-8 -*-
"""Environment health checker — powered by channels.

Each channel knows how to check itself. Doctor just collects the results.
"""

from typing import Dict

from rich.markup import escape

from agent_reach.channels import get_all_channels
from agent_reach.config import Config
from agent_reach.utils.text import scrub_url_credentials


def check_all(config: Config) -> Dict[str, dict]:
    """Check all channels and return status dict.

    A single misbehaving channel must never take the whole report down,
    so per-channel exceptions degrade to status="error".
    """
    results = {}
    for ch in get_all_channels():
        try:
            status, message = ch.check(config)
            active = getattr(ch, "active_backend", None)
        except Exception as e:  # noqa: BLE001 — doctor must survive any channel
            # Channels are registry singletons: a stale active_backend from a
            # previous check must not leak into an errored result.
            status = "error"
            message = f"体检异常：{e}"
            active = None
        # Doctor is the final output boundary for both expected channel
        # messages and unexpected exceptions. Upstream probe output can echo a
        # configured URL, so scrub every path before JSON/text rendering.
        message = scrub_url_credentials(message)
        results[ch.name] = {
            "status": status,
            "name": ch.description,
            "message": message,
            "tier": ch.tier,
            "backends": ch.backends,
            "active_backend": active,
        }
    return results


def _name_msg(r: dict, escape) -> str:
    """Render one channel line; show the active backend when there is a choice."""
    text = f"[bold]{escape(r['name'])}[/bold] — {escape(r['message'])}"
    active = r.get("active_backend")
    if active and len(r.get("backends", [])) > 1:
        text += f" [dim]（当前后端：{escape(active)}）[/dim]"
    return text


def format_report(results: Dict[str, dict]) -> str:
    """Format results as a readable text report (with Rich markup)."""
    lines = []
    lines.append("[bold cyan]Agent Reach 状态[/bold cyan]")
    lines.append("[cyan]" + "=" * 40 + "[/cyan]")
    lines.append("图例：[green]✅[/green] 可用  [yellow][!][/yellow] 需配置/登录或待验证  [red][X][/red] 未安装或检查失败")

    ok_count = sum(1 for r in results.values() if r["status"] == "ok")
    total = len(results)

    # Tier 0 — zero config
    lines.append("")
    lines.append("[bold]✅ 装好即用：[/bold]")
    for key, r in results.items():
        if r["tier"] == 0:
            name_msg = _name_msg(r, escape)
            if r["status"] == "ok":
                lines.append(f"  [green]✅[/green] {name_msg}")
            elif r["status"] == "warn":
                lines.append(f"  [yellow][!][/yellow]  {name_msg}")
            elif r["status"] in ("off", "error"):
                lines.append(f"  [red][X][/red]  {name_msg}")

    # Optional channels: warnings/errors carry actionable diagnostics. Only
    # explicitly absent channels belong in the compact installation summary.
    optional = [r for r in results.values() if r["tier"] in (1, 2)]
    visible = [r for r in optional if r["status"] != "off"]
    if visible:
        lines.append("")
        lines.append("[bold]可选渠道：[/bold]")
        for r in visible:
            marker = {
                "ok": "[green]✅[/green]",
                "warn": "[yellow][!][/yellow]",
            }.get(r["status"], "[red][X][/red]")
            lines.append(f"  {marker} {_name_msg(r, escape)}")

    lines.append("")
    status_color = "green" if ok_count == total else ("yellow" if ok_count > 0 else "red")
    lines.append(f"状态：[{status_color}]{ok_count}/{total}[/{status_color}] 个渠道可用")

    # Summarize absent optional channels in one line instead of listing each.
    all_inactive = [r for r in optional if r["status"] == "off"]
    if all_inactive:
        names = [r["name"] for r in all_inactive]
        lines.append(
            f"还有 {len(names)} 个可选渠道可以解锁（{'、'.join(names)}），"
            "告诉你的 Agent「帮我装 XXX」即可"
        )

    # Security check: config file permissions (Unix only)
    import stat
    import sys

    config_path = Config.CONFIG_DIR / "config.yaml"
    if config_path.exists() and sys.platform != "win32":
        try:
            mode = config_path.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                lines.append("")
                lines.append(
                    "[bold red][!]  安全提示：config.yaml 权限过宽（其他用户可读）[/bold red]"
                )
                lines.append("   修复：chmod 600 ~/.agent-reach/config.yaml")
        except OSError:
            pass

    return "\n".join(lines)
