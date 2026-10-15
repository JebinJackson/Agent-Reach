# Agent Reach — Update Guide

## For Humans

Copy this to your AI Agent:

```
帮我更新 Agent Reach：https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/update.md
```

Or in English:

```
Update Agent Reach: https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/update.md
```

---

## For AI Agents

### ⚠️ Workspace Rules

**Never create files, clone repos, or run commands in the agent workspace.** Use `/tmp/` for temporary work and `~/.agent-reach/` for persistent data.

### Goal

Update Agent Reach to the latest version, refresh upstream tools, migrate from retired backends, and verify everything works. The user should not need to do anything manually (except things only a human can do, like clicking a browser-extension install button).

### Step 1: Check current version

```bash
agent-reach check-update
```

`check-update` compares **version strings** only. `main` often moves ahead
while `pyproject.toml` still declares the same version, so "已是最新版本" does
not prove the installed code matches `main`. Unless the user only wants tagged
releases, continue with Step 2 anyway — it is safe to re-run.

### Step 2: Update Agent Reach package

First find out **where the running package actually lives** — do not guess
from which directories exist (an empty `~/.agent-reach-venv` stub can exist
next to a real user-site install):

```bash
python3 -c "import agent_reach, os; print(os.path.dirname(agent_reach.__file__))"
which agent-reach
```

Then use the command that matches that location:

| Install location | Update command |
|---|---|
| pipx (`~/.local/share/pipx/venvs/agent-reach/...` or `~/.local/pipx/...`) | `pipx install --force https://github.com/Panniantong/agent-reach/archive/main.zip` |
| `~/.agent-reach-venv/...` (only if `agent_reach` is really inside it) | `~/.agent-reach-venv/bin/python -m pip install --upgrade --force-reinstall --no-deps <URL>` |
| user site (`~/.local/lib/python3.X/site-packages`) | `python3 -m pip install --user --upgrade --force-reinstall --no-deps <URL>` |
| anything else (system / other venv) | `python3 -m pip install --upgrade --force-reinstall --no-deps <URL>` |

`<URL>` = `https://github.com/Panniantong/agent-reach/archive/main.zip`

> **Why `--force-reinstall --no-deps`?** A plain `pip install --upgrade <URL>`
> prints `Requirement already satisfied` and installs nothing whenever `main`
> still declares the same version as the installed copy — even if `main` has
> many new commits. `--force-reinstall` makes pip replace the package files;
> `--no-deps` keeps it from reinstalling every dependency. If `main` added new
> dependencies, follow up with the same command without
> `--force-reinstall --no-deps` to pull them in.
>
> If pip complains about `externally-managed-environment` (PEP 668) for a
> user-site install, add `--break-system-packages` to the user-site command —
> it only writes to the user site, not the system Python.

Verify the update really landed (the file listing should show today's date):

```bash
python3 -c "import agent_reach, os; print(os.path.dirname(agent_reach.__file__))" | xargs ls -l --time-style=long-iso | head -5
```

### Step 3: Refresh upstream tools

Run these to keep installed tools current. **Only upgrade what is already
installed — do not install new tools the user never asked for** (the one
exception: OpenCLI on desktop, see below).

```bash
# Python-based CLIs the user already has (upgrade keeps signatures fresh)
which twitter >/dev/null 2>&1 && { pipx upgrade twitter-cli 2>/dev/null || uv tool upgrade twitter-cli 2>/dev/null; }
which bili    >/dev/null 2>&1 && { pipx upgrade bilibili-cli 2>/dev/null || uv tool upgrade bilibili-cli 2>/dev/null; }
which xhs     >/dev/null 2>&1 && { pipx upgrade xiaohongshu-cli 2>/dev/null || uv tool upgrade xiaohongshu-cli 2>/dev/null; }
which yt-dlp  >/dev/null 2>&1 && { pipx install --force 'yt-dlp[default]' 2>/dev/null || uv tool install --force 'yt-dlp[default]' 2>/dev/null || python -m pip install -U 'yt-dlp[default]' 2>/dev/null; }

# rdt-cli is pinned to a git source (PyPI lags upstream) — same pin as the code's _RDT_GIT_SOURCE
which rdt >/dev/null 2>&1 && pipx install --force 'git+https://github.com/public-clis/rdt-cli.git@5e4fb3720d5c174e976cd425ccc3b879d52cac66' 2>/dev/null

# npm-based
which mcporter >/dev/null 2>&1 && npm update -g mcporter 2>/dev/null
which opencli  >/dev/null 2>&1 && npm update -g @jackwener/opencli 2>/dev/null
```

**Desktop users without OpenCLI**: since v1.5.0 OpenCLI is the preferred
backend for 小红书/Reddit (and adds B站 subtitles). Offer it once. For
小红书, OpenCLI may use only an existing Chrome session explicitly controlled
by the user. The update must never log the user in or read browser cookies:

> "这次更新引入了 OpenCLI 后端。要装吗？装完只需你在 Chrome 商店点一次
> 『添加扩展』。小红书 OpenCLI 只使用你已有的 Chrome 会话；如果没有现成
> 会话，我不会自动登录，会改用 Cookie-Editor 配置 MCP / 存量工具。"

If yes: `agent-reach install --system --channels opencli` and guide them through the
extension click. If no, everything keeps working on existing backends.

### Step 4: Coexistence (DO NOT uninstall old tools)

**Never uninstall tools the user already has.** Retired backends (e.g. yt-dlp
no longer serves Bilibili; xhs-cli is no longer installed by default) keep
working as fallbacks where they still function. Agent Reach routes around
them automatically — removal is the user's call, not yours.

### Step 5: Verify

```bash
agent-reach version
agent-reach doctor
```

Running `agent-reach doctor` (text mode) also makes sure an Agent Reach skill
exists in detected agent skill directories. If the user already has a skill
there, doctor preserves it instead of overwriting local customizations. Use
`agent-reach skill --install` when you explicitly want to refresh the bundled
skill files.

Check the doctor output:

- Every channel shows ✅ / [!] with a clear message, and multi-backend
  channels (小红书/Reddit/B站/Twitter) report `当前后端：…`
- If a previously-working channel now shows [X]/error, the message contains
  the exact fix (e.g. a venv-reinstall prescription) — run it, then re-check
- `--json` gives the same data machine-readably (`active_backend` per channel)

### Step 6: Report to user

Tell the user:

1. What version they're on now (`agent-reach version`)
2. How many channels are available, and which backend each multi-backend
   platform is using (from doctor)
3. Anything that needs their action (e.g. a Chrome extension click, or a
   manual Cookie-Editor export when XiaoHongShu uses MCP / a legacy tool)
4. What changed in this update (release notes shown by `check-update`)

Done.
