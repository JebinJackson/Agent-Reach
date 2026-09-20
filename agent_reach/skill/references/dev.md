# 开发工具

GitHub CLI 

## GitHub (gh CLI)

GitHub 官方命令行工具，用于仓库、Issue、PR、Actions、Release 以及 API 访问。

```bash
# 认证
gh auth login
gh auth status

# 搜索
gh search repos "query" --sort stars --limit 10
gh search code "query" --language python

# 仓库
gh repo view owner/repo
gh repo clone owner/repo
gh repo create my-repo --private
gh repo fork owner/repo
gh repo fork owner/repo --clone
gh repo sync owner/repo

# Issues
gh issue list -R owner/repo --state open
gh issue view 123 -R owner/repo
gh issue create -R owner/repo --title "Title" --body "Body"

# Pull Requests
gh pr list -R owner/repo --state open
gh pr view 123 -R owner/repo
gh pr create -R owner/repo --title "Title" --body "Body"
gh pr checks 123 --repo owner/repo

# Actions / CI
gh run list --repo owner/repo --limit 10
gh run view <run-id> --repo owner/repo
gh run view <run-id> --repo owner/repo --log-failed
gh workflow list --repo owner/repo

# Releases
gh release list -R owner/repo
gh release create v1.0.0

# API
gh api /user
gh api repos/owner/repo

# JSON 输出
gh issue list --repo owner/repo --json number,title --jq '.[] | "\(.number): \(.title)"'
```


## Hacker News (公开 API，无需配置)

零配置：没有 Key、没有登录、没有 CLI。Firebase API 负责读取，搜索走 Algolia
（Firebase 没有搜索端点）。两者都免费公开。

### 热榜 / 最新 / 最佳 / Ask / Show / Job

```bash
# 返回 500 个 id；条目要逐个取（见下方「取条目」）
curl -s "https://hacker-news.firebaseio.com/v0/topstories.json" -H "User-Agent: agent-reach/1.0"

# 可选 feed：topstories / newstories / beststories / askstories / showstories / jobstories
curl -s "https://hacker-news.firebaseio.com/v0/beststories.json" -H "User-Agent: agent-reach/1.0"
```

### 取条目

```bash
# 帖子或评论；item id 从 https://news.ycombinator.com/item?id=ID 获取
curl -s "https://hacker-news.firebaseio.com/v0/item/1.json" -H "User-Agent: agent-reach/1.0"
```

> ⚠️ **不要直接顺序拉 500 条。** `topstories.json` 只给 id 列表，每条要一次请求。
> 想要 30 条就取前 30~45 个 id（要留余量：列表里常混有已删除/被 flag 的条目，
> 按 id 数量取满 30 条，过滤后可能只剩 25 条）。并发拉取，但别超过 8 个并发。

### 用户资料

```bash
curl -s "https://hacker-news.firebaseio.com/v0/user/pg.json" -H "User-Agent: agent-reach/1.0"
```

### 搜索

```bash
# Firebase 没有搜索端点，用 Algolia（HN 站内搜索同款，公开免 Key）
curl -s "https://hn.algolia.com/api/v1/search?query=show+hn&tags=story&hitsPerPage=10" \
  -H "User-Agent: agent-reach/1.0"
```

### 字段说明

| 字段 | 含义 |
|------|------|
| `title` / `url` | Ask HN、文本帖没有外链，`url` 为空 —— 用 `https://news.ycombinator.com/item?id=<id>` |
| `score` / `descendants` | 分数 / 评论总数 |
| `kids` | **直接回复的 id 列表**，不是评论内容；要内容得再取一次 |
| `text` | HTML 片段，需要转纯文本（保留链接目标，别只删标签） |
| `deleted` / `dead` | 作者删除 / 被 flag；两种都应当跳过而不是当空条目输出 |

## 选择指南

| 工具 | 来源 | 用途 |
|-----|------|------|
| gh CLI | agent-reach | Git 操作 |
| Hacker News API | agent-reach | 开发者热榜、讨论、HN 站内搜索（零配置） |
| zread | my-mcp-tools | 读仓库内容 |
| context7 | my-mcp-tools | 查技术文档 |
