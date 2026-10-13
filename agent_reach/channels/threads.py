"""Threads public channel backed by the bundled read-only OpenCLI plugin."""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from ._opencli_site import OpenCLISiteChannel


class ThreadsChannel(OpenCLISiteChannel):
    name = "threads"
    description = "Threads 公開搜尋、帳號及單篇貼文"
    site = "threads"
    domains = ("threads.com", "threads.net")
    usage = "opencli threads search/profile/read -f json"
    login_hint = "threads.com"

    def check(self, config=None):
        status, message = super().check(config)
        if status in ("off", "error"):
            return status, message
        try:
            self._run(["threads", "search", "--help"], parse=False)
        except RuntimeError:
            return "warn", "OpenCLI 可執行，但本機 Threads 插件尚未註冊或無法載入"
        return "warn", "Threads 插件已註冊；登入態與目標內容須另做實查，doctor 不宣稱讀取成功"

    @staticmethod
    def _run(args, parse=True):
        cli = shutil.which("opencli.cmd") or shutil.which("opencli")
        if not cli:
            raise RuntimeError("OpenCLI unavailable")
        if cli.lower().endswith((".cmd", ".bat", ".ps1")):
            entry = Path(cli).parent / "node_modules/@jackwener/opencli/dist/src/main.js"
            node = shutil.which("node")
            if not node or not entry.is_file():
                raise RuntimeError(
                    "Cannot resolve OpenCLI Node entrypoint; refusing shell execution"
                )
            command = [node, str(entry), *args]
        else:
            command = [cli, *args]
        try:
            task_env = dict(os.environ, OPENCLI_BROWSER_COMMAND_TIMEOUT="120")
            r = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=160,
                stdin=subprocess.DEVNULL,
                env=task_env,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError("OpenCLI launch failed or timed out") from e
        if r.returncode:
            detail = re.sub(r"https?://\S+", "[URL omitted]", r.stderr or r.stdout)[-400:]
            raise RuntimeError(f"OpenCLI command failed (exit {r.returncode}): {detail}")
        if not parse:
            return r.stdout
        try:
            data = json.loads(r.stdout)
        except ValueError as e:
            raise RuntimeError("OpenCLI did not return JSON") from e
        if isinstance(data, dict):
            data = data.get("data", data.get("rows"))
        if not isinstance(data, list) or not data:
            raise RuntimeError("No public post data returned")
        if not all(
            isinstance(row, dict)
            and row.get("text")
            and row.get("url", "").startswith("https://www.threads.com/@")
            for row in data
        ):
            raise RuntimeError("Invalid public post rows")
        return data

    def search(self, query, limit=10, since=None, until=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 100:
            raise ValueError("Query must be 1..100 characters")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be 1..10")
        dates = {
            name: date.fromisoformat(value)
            for name, value in [("since", since), ("until", until)]
            if value
        }
        if "since" in dates and "until" in dates and dates["since"] > dates["until"]:
            raise ValueError("since must not follow until")
        args = ["threads", "search", query, "--limit", str(limit), "-f", "json"]
        for name, value in dates.items():
            args.extend(["--" + name, value.isoformat()])
        return self._run(args)

    def profile(self, username, limit=3):
        username = username.removeprefix("@")
        if (
            not re.fullmatch(r"[a-zA-Z0-9._]{1,60}", username)
            or type(limit) is not int
            or not 1 <= limit <= 10
        ):
            raise ValueError("Invalid username or limit")
        return self._run(["threads", "profile", username, "--limit", str(limit), "-f", "json"])

    def read(self, url):
        u = urlsplit(url)
        if (
            u.scheme != "https"
            or u.netloc not in ("threads.com", "www.threads.com", "threads.net", "www.threads.net")
            or not re.fullmatch(r"/@[a-zA-Z0-9._]+/post/[a-zA-Z0-9_-]+/?", u.path)
            or u.query
            or u.fragment
        ):
            raise ValueError("Use a canonical public Threads post URL")
        return self._run(
            ["threads", "read", "https://www.threads.com" + u.path.rstrip("/"), "-f", "json"]
        )
