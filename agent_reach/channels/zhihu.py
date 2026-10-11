"""Zhihu official Open Platform CLI — search, hot list and answer summaries."""

from agent_reach.backends.zhihu import (
    INSTALL_HINT,
    ZhihuCLIError,
    ZhihuCLIIncompatible,
    binary_path,
    inspect_binary,
    run_json,
)
from agent_reach.utils.url import host_matches

from .base import Channel


class ZhihuChannel(Channel):
    name = "zhihu"
    description = "知乎搜索、热榜和回答摘要"
    backends = ["zhihu-cli (official)"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "zhihu.com")

    def check(self, config=None):
        self.active_backend = None
        try:
            binary = binary_path()
            if not binary.is_file():
                return "off", f"知乎官方 CLI 未安装。运行：{INSTALL_HINT}"
            version = inspect_binary(binary)
        except ZhihuCLIIncompatible as exc:
            return "warn", str(exc)
        except ZhihuCLIError as exc:
            return "error", str(exc)
        except OSError:
            return "error", f"无法检查知乎官方 CLI；修复：{INSTALL_HINT}"

        prefix = f"知乎官方 CLI {version} 可执行：{binary}。"
        try:
            auth = run_json(binary, "auth", "status")
        except ZhihuCLIError:
            return "warn", prefix + "本地认证状态未知；请检查系统凭据库或 ZHIHU_ACCESS_SECRET。"
        if auth.get("ok") is not True:
            return "warn", prefix + "认证状态格式异常，未验证。"
        if auth.get("source") in ("environment", "keychain"):
            return "warn", prefix + (
                "Access Secret 已配置；Doctor 不联网验证、不查询内容，未标记为可用。"
                "Agent 可按知乎使用指南执行用户要求的只读命令。"
            )
        return "warn", prefix + (
            "未检测到可用的 Access Secret。请在 https://developer.zhihu.com/profile 申请，"
            "通过该 CLI 的 `auth set --secret-stdin` 保存到系统凭据库；"
            "无凭据库的服务器/容器通过进程级 ZHIHU_ACCESS_SECRET 注入。"
            "无需浏览器 Cookie，勿将 Secret 写入 Agent Reach 配置。"
        )
