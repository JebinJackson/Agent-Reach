# 知乎 / Zhihu — official CLI

Backend: **知乎官方 `zhihu-cli`**, not a community `zhihu` executable or a
same-named pip/npm package. This channel covers search, hot lists and question
answer summaries. Prefer an installed dedicated Zhihu skill for broader tasks.

Official documentation: https://developer.zhihu.com/docs?key=zhihu_cli

## Install and locate

```bash
agent-reach install --system --channels=zhihu
agent-reach doctor --json
```

Only `--system` installs the official binary. Default / `--safe` / `--dry-run`
checks do not download or modify anything. Authentication is a separate step.
The installer verifies the official HTTPS artifact's size, SHA-256 and version,
and reuses a compatible existing install (minimum CLI version: 0.6.1).

Use the absolute binary path printed by installation or the Zhihu doctor
message. Official installation does **not** add the executable to PATH:

| OS | Default binary path |
|---|---|
| Windows x64 | `%LOCALAPPDATA%\ZhihuCLI\current\zhihu-cli.exe` |
| macOS arm64 / amd64 | `~/Library/Application Support/zhihu-cli/current/zhihu-cli` |
| Linux arm64 / amd64 | `${XDG_DATA_HOME:-$HOME/.local/share}/zhihu-cli/current/zhihu-cli` |

`ZHIHU_CLI_HOME` overrides the data directory and must be absolute. In that case
use `<ZHIHU_CLI_HOME>/current/zhihu-cli` (Windows: `zhihu-cli.exe`). Do not fall
back to an unrelated executable found on PATH.

In the examples below `<CLI>` means this absolute path. In PowerShell execute
it as `& '<absolute-path>' ...`; in POSIX shells as `'<absolute-path>' ...`.

## Authentication / 认证

Use the account owner's **Access Secret** from https://developer.zhihu.com/profile.
This channel does not import browser cookies or require a Chrome/CDP session.

```text
<CLI> auth set --secret-stdin
<CLI> auth status
```

Pass the user-provided Secret through the child process's stdin, not command
arguments, logs, chat output or shell history. The official CLI validates it
online before saving it in the OS credential store. Do not duplicate it in
Agent Reach's config.yaml, project files or Skill directory.

Credential precedence: process-level `ZHIHU_ACCESS_SECRET`, then OS credential
store (macOS Keychain / Windows Credential Manager / Linux Secret Service).
Headless Linux or sandboxed hosts should inject the environment variable via
their secret store. An invalid environment value does not fall back to keychain.

Doctor runs only `version`, `capabilities` and local `auth status`: no content
requests, online verification, quota query, login, logout or upgrade. A `warn`
with `active_backend: null` can mean installed/configured but unverified. Read
the message: missing/broken/incompatible installations must be fixed first.
When configured, execute only the read-only command required by the user's task.
`auth status --verify` is a separate online check and may consume quota; it is
not a default health probe.

## Content commands / 获取内容

```text
<CLI> search zhihu --query "AI Agent 的实际使用经验" --count 5
<CLI> hot --limit 10
<CLI> question answers --question-url "https://www.zhihu.com/question/123" --offset 0 --limit 10
```

- Search `--count`: 1–10. Hot list `--limit`: 1–30. Answer summaries `--limit`: 1–50.
- stdout is JSON; stderr is diagnostic output. Check the exit code and business
  error fields before interpreting content. HTTP 200 does not imply success.
- Preserve source titles, authors, summaries and original URLs. Search snippets
  and answer `Summary` are **not full text**. This channel does not promise
  arbitrary answer/article full-text retrieval.
- An empty successful result is valid. For more answers, use `Paging.NextOffset`
  only when `Paging.IsEnd=false` and the user needs more. Do not calculate offsets
  from the returned item count; stop if the next offset is missing.
- Do not automatically fetch personal creations, follows, favorites, knowledge
  bases, or call the generated-answer API as part of search.

## Failures / 故障处理

| Signal | Action |
|---|---|
| `AUTH_REQUIRED` / `AUTH_INVALID` | Ask the user to configure/replace their Access Secret; never echo it |
| `KEYCHAIN_UNAVAILABLE` | Use host-managed process-level `ZHIHU_ACCESS_SECRET` |
| `ENV_SHADOWS_KEYCHAIN` | Explain that the environment value takes precedence |
| Exit 4 / `Code: 30001` or `30002` | Stop retries; report rate limit or exhausted quota |
| Exit 5 / `NETWORK_ERROR` / `TIMEOUT` | At most one retry of the requested read-only query |
| Other nonzero exit / malformed JSON | Report the error; do not present it as no results |

Quota is account-dependent. Query `<CLI> quota` only when the user needs it;
do not claim unlimited/free usage or poll quota before every request. Updates
are explicit; Doctor never runs `upgrade` or installs the separate official Skill.
