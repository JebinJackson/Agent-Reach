# 网页阅读

通用网页、RSS。

## 通用网页 (Jina Reader)

```bash
# 读取任意网页内容
curl -s "https://r.jina.ai/URL"

# 示例
curl -s "https://r.jina.ai/https://example.com/article"
```

**适用场景**: 大多数网页可以直接用 Jina Reader 读取。

## Exa 抓取（Jina 失败时的兜底）

`agent-reach install` 已配置好 Exa（见 [search.md](search.md)），可以直接抓取已知 URL 的正文：

```bash
# 读取一个或多个 URL（Markdown），maxCharacters 控制长度
mcporter call exa.web_fetch_exa urls='["https://example.com"]' maxCharacters=5000
```

**适用场景**: Jina 报错、被站点拦截或返回空内容时；一次读多个 URL 时。

## RSS (feedparser)

`feedparser` 是 Agent Reach 的依赖，装在 **Agent Reach 自己的 Python 环境**里。推荐的 `pipx install` 会把它隔离在 pipx venv 中，系统 `python3` / `python` 通常 `import feedparser` 会失败（Windows 上 `python3` 还可能是 Microsoft Store 占位程序）。用 Agent Reach 的解释器运行：

```bash
# pipx 安装：定位 Agent Reach 的 venv（Linux/macOS 是 bin/，Windows 是 Scripts/）；pip 安装则用当前 python
if command -v pipx >/dev/null 2>&1; then
  AR_VENV="$(pipx environment --value PIPX_LOCAL_VENVS)/agent-reach"
  AR_PY="$AR_VENV/bin/python"; [ -x "$AR_PY" ] || AR_PY="$AR_VENV/Scripts/python.exe"
else
  AR_PY=python
fi
"$AR_PY" -c "
import feedparser
for e in feedparser.parse('FEED_URL').entries[:5]:
    print(f'{e.title} — {e.link}')
"
```

**适用场景**: 订阅博客、新闻源、播客等 RSS feed。

## 选择指南

| 场景 | 推荐工具 |
|-----|---------|
| 通用网页 | Jina Reader (`curl r.jina.ai`) |
| Jina 失败 / 批量 URL | Exa `web_fetch_exa` |
| RSS 订阅 | feedparser（Agent Reach 的解释器） |
