# -*- coding: utf-8 -*-
"""Threads — public profiles and posts via Jina Reader.

OpenCLI has no Threads adapter yet (jackwener/OpenCLI#2564), so only the
logged-out public pages are covered: a profile's recent posts and a single
post's text. Search and full reply threads need a logged-in session and are
not supported until an upstream backend exists.
"""

from agent_reach.utils.url import host_matches

from .base import Channel


class ThreadsChannel(Channel):
    name = "threads"
    description = "Threads 公开主页和帖子"
    backends = ["Jina Reader"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "threads.com", "threads.net")

    def check(self, config=None):
        # 与 web 渠道同一后端：无本地命令、不做网络探测
        self.active_backend = self.backends[0]
        return "ok", (
            "通过 Jina Reader 读取公开主页和帖子"
            "（curl https://r.jina.ai/https://www.threads.com/@USER）；"
            "搜索和完整回复需要登录态，暂不支持"
        )
