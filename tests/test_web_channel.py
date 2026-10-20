# -*- coding: utf-8 -*-
"""Dedicated tests for the ``web`` channel.

``web`` is the tier-0 catch-all: ``can_handle`` must accept *anything* so it
can back-stop every other channel, ``check`` must report ready without touching
the network (it is the zero-overhead fallback), and ``read`` must normalise the
URL before handing it to Jina Reader. Follow-up to #331 / #360 / #361,
completing dedicated coverage for the channels that still lacked it.
"""

from unittest.mock import MagicMock, patch

import pytest

from agent_reach.channels.web import _UA, WebChannel

_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _resp(body=b"# Example\nfull text\n"):
    """A urlopen() return value usable as a context manager."""
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


# --- can_handle: universal fallback contract ---

def test_can_handle_accepts_any_url():
    channel = WebChannel()
    for sample in [
        "https://example.com",
        "http://example.com/path?q=1",
        "example.com",
        "ftp://files.example.com/readme.txt",
        "not a url at all",
        "",
    ]:
        assert channel.can_handle(sample) is True, sample


# --- check: ready without any network probe (零开销兜底) ---



# --- read: URL normalisation + Jina Reader request shape ---

def test_read_prepends_https_for_schemeless_url():
    channel = WebChannel()
    with patch("urllib.request.urlopen", return_value=_resp()) as mock_open:
        out = channel.read("example.com/article")
    req = mock_open.call_args.args[0]
    assert req.full_url == "https://r.jina.ai/https://example.com/article"
    assert out == "# Example\nfull text\n"


def test_read_preserves_existing_http_scheme():
    channel = WebChannel()
    with patch("urllib.request.urlopen", return_value=_resp()) as mock_open:
        channel.read("http://example.com")
    req = mock_open.call_args.args[0]
    # http:// must be kept as-is, not coerced to https:// nor double-prefixed.
    assert req.full_url == "https://r.jina.ai/http://example.com"


def test_read_preserves_existing_https_scheme():
    channel = WebChannel()
    with patch("urllib.request.urlopen", return_value=_resp()) as mock_open:
        channel.read("https://example.com/deep/path")
    req = mock_open.call_args.args[0]
    assert req.full_url == "https://r.jina.ai/https://example.com/deep/path"


def test_read_sends_expected_headers_and_timeout():
    channel = WebChannel()
    with patch("urllib.request.urlopen", return_value=_resp()) as mock_open:
        channel.read("https://example.com")
    req = mock_open.call_args.args[0]
    assert req.headers == {"User-agent": _UA, "Accept": "text/plain"}
    assert mock_open.call_args.kwargs["timeout"] == 30


def test_read_decodes_utf8_body():
    channel = WebChannel()
    with patch("urllib.request.urlopen", return_value=_resp("café ☕\n".encode("utf-8"))):
        out = channel.read("https://example.com")
    assert out == "café ☕\n"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/admin",
        "http://intranet/admin",
        "http://home.arpa/admin",
        "http://metadata.google.internal/latest/meta-data",
        "http://127.0.0.1/private",
        "http://127.1/private",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1/private",
        "http://0/private",
        "http://2130706433/private",
        "http://0x7f000001/private",
        "http://0177.0.0.1/private",
        "http://2852039166/latest/meta-data",
        "http://0xA9FEA9FE/latest/meta-data",
        "http://[::1]/private",
        "http://[::ffff:127.0.0.1]/private",
        "http://localhost./admin",
        "http://127.0.0.1\\example.com/private",
        "https://user:password@example.com/private",
    ],
)
def test_read_rejects_non_public_urls_before_network(url):
    channel = WebChannel()

    with patch("urllib.request.urlopen") as mock_open:
        with pytest.raises(ValueError, match="public HTTP"):
            channel.read(url)

    mock_open.assert_not_called()


@pytest.mark.parametrize("url", ["https://8.8.8.8/page", "http://010.010.010.010/page"])
def test_read_allows_public_literal_addresses(url):
    channel = WebChannel()
    with patch("urllib.request.urlopen", return_value=_resp()) as mock_open:
        channel.read(url)
    mock_open.assert_called_once()


def test_read_accepts_response_at_exact_size_limit():
    channel = WebChannel()
    response = _resp(b"x" * _MAX_RESPONSE_BYTES)

    with patch("urllib.request.urlopen", return_value=response):
        out = channel.read("https://example.com/exact")

    assert len(out) == _MAX_RESPONSE_BYTES
    response.__enter__.return_value.read.assert_called_once_with(
        _MAX_RESPONSE_BYTES + 1
    )


def test_read_rejects_oversized_reader_response():
    channel = WebChannel()
    response = _resp(b"x" * (_MAX_RESPONSE_BYTES + 1))

    with patch("urllib.request.urlopen", return_value=response):
        with pytest.raises(ValueError, match="response exceeds"):
            channel.read("https://example.com/large")

    response.__enter__.return_value.read.assert_called_once_with(
        _MAX_RESPONSE_BYTES + 1
    )


@pytest.mark.parametrize(
    "body",
    [
        (
            "Title: Just a moment...\n\n"
            "URL Source: https://imginn.com/instagram/\n\n"
            "Warning: This page maybe requiring CAPTCHA\n\n"
            "Markdown Content:\n\n"
            "## Performing security verification\n"
        ),
        (
            "Title: Attention Required! | Cloudflare\n\n"
            "Sorry, you have been blocked.\n\nRay ID: 1234567890abcdef\n"
        ),
    ],
)
def test_read_rejects_high_confidence_antibot_pages(body):
    channel = WebChannel()

    with patch(
        "urllib.request.urlopen", return_value=_resp(body.encode("utf-8"))
    ) as mock_open:
        with pytest.raises(RuntimeError, match="反爬验证页"):
            channel.read("https://example.com/protected")

    mock_open.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [
        "# A guide to security verification\n",
        "# DDoS protection explained\n",
        "# Checking your browser automation\n",
        "# Please turn JavaScript on for progressive enhancement\n",
        "# A history of cf-browser-verify\n",
        "Title: Just a moment...\n\nA short-story review.\n",
    ],
)
def test_read_does_not_reject_single_generic_antibot_terms(body):
    channel = WebChannel()

    with patch("urllib.request.urlopen", return_value=_resp(body.encode("utf-8"))):
        assert channel.read("https://example.com/article") == body


def test_antibot_detection_has_a_fixed_scan_window():
    channel = WebChannel()
    body = (
        "x" * 4096
        + "Warning: requiring CAPTCHA\n"
        + "Title: Just a moment...\n"
        + "## Performing security verification\n"
    )

    with patch("urllib.request.urlopen", return_value=_resp(body.encode("utf-8"))):
        assert channel.read("https://example.com/long-article") == body


# --- DeepSeek backends (search / read with the user's DeepSeek key) ---


class _FakeConfig:
    def __init__(self, key):
        self._key = key

    def __call__(self, *args, **kwargs):
        return self

    def get(self, name, default=None):
        return self._key if name == "deepseek_api_key" else default


def _deepseek(monkeypatch, response, key="sk-test"):
    """Point the web module at a fake key and capture DeepSeek requests."""
    import json

    from agent_reach.channels import web

    calls = []

    def fake_http_request(url, *, timeout, data=None, headers=None, **_):
        calls.append({"url": url, "body": json.loads(data), "headers": headers, "timeout": timeout})
        return json.dumps(response).encode("utf-8")

    monkeypatch.setattr(web, "Config", _FakeConfig(key))
    monkeypatch.setattr(web, "http_request", fake_http_request)
    return calls


def test_deepseek_search_request_and_items(monkeypatch):
    calls = _deepseek(monkeypatch, {"results": [
        {"title": "A", "url": "https://a.example", "snippet": "short", "summary": "long abstract",
         "site_name": "Site A", "date": "2026-10-16T16:00:00+00:00", "rerank_score": 2.3},
        {"title": "B", "url": "https://b.example", "snippet": "only snippet", "summary": "",
         "site_name": None, "date": None},
    ]})

    items = WebChannel()._deepseek_search(query="q" * 300, limit=99, timeout=10)

    call = calls[0]
    assert call["url"] == "https://api.deepseek.com/v1/tools/search"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["body"] == {"query": "q" * 128, "count": 50}
    assert [i["text"] for i in items] == ["long abstract", "only snippet"]
    assert items[0]["author"] == "Site A"
    assert items[0]["published_at"] == "2026-10-16T16:00:00+00:00"
    assert items[0]["raw"]["rerank_score"] == 2.3
    assert items[1]["published_at"] is None


def test_deepseek_search_trims_to_limit(monkeypatch):
    results = [{"title": str(n), "url": f"https://{n}.example"} for n in range(5)]
    calls = _deepseek(monkeypatch, {"results": results})

    items = WebChannel()._deepseek_search(query="q", limit=2, timeout=10)

    assert calls[0]["body"]["count"] == 2
    assert [i["title"] for i in items] == ["0", "1"]


def test_deepseek_without_key_needs_setup(monkeypatch):
    from agent_reach.runtime.result import ReachError

    calls = _deepseek(monkeypatch, {"results": []}, key=None)

    with pytest.raises(ReachError) as exc:
        WebChannel()._deepseek_search(query="q", limit=2, timeout=10)
    assert exc.value.code == "NEED_SETUP"
    assert "configure deepseek-key" in exc.value.hint
    assert calls == []


def test_search_falls_back_to_deepseek_when_exa_fails(monkeypatch):
    from agent_reach.channels import web
    from agent_reach.runtime.dispatch import run_action
    from agent_reach.runtime.result import ReachError

    def broken_exa(*args, **kwargs):
        raise ReachError("UPSTREAM_BROKEN", "Exa: down")

    monkeypatch.setattr(web, "_exa_call", broken_exa)
    _deepseek(monkeypatch, {"results": [{"title": "A", "url": "https://a.example", "summary": "s"}]})

    out = run_action(WebChannel(), "search", {"query": "q"})

    assert out["ok"] and out["backend"] == "deepseek"
    assert [a["backend"] for a in out["meta"]["attempts"]] == ["exa", "deepseek"]


def test_search_without_deepseek_key_reports_the_exa_error(monkeypatch):
    from agent_reach.channels import web
    from agent_reach.runtime.dispatch import run_action
    from agent_reach.runtime.result import ReachError

    def broken_exa(*args, **kwargs):
        raise ReachError("RATE_LIMITED", "Exa: rate limit")

    monkeypatch.setattr(web, "_exa_call", broken_exa)
    _deepseek(monkeypatch, {"results": []}, key=None)

    out = run_action(WebChannel(), "search", {"query": "q"})

    assert out["error"]["code"] == "RATE_LIMITED"


def test_deepseek_read_fetches_markdown(monkeypatch):
    calls = _deepseek(monkeypatch, {"title": "Example", "content": "# Example\n\nbody\n"})

    items = WebChannel()._deepseek_read(target="example.com/page", timeout=10)

    assert calls[0]["url"] == "https://api.deepseek.com/v1/tools/fetch"
    assert calls[0]["body"] == {"url": "https://example.com/page"}
    assert items == [{"title": "Example", "url": "https://example.com/page", "author": None,
                      "published_at": None, "text": "# Example\n\nbody", "raw": None}]


def test_deepseek_read_empty_content_is_upstream_broken(monkeypatch):
    from agent_reach.runtime.result import ReachError

    _deepseek(monkeypatch, {"title": "", "content": "  "})

    with pytest.raises(ReachError) as exc:
        WebChannel()._deepseek_read(target="https://example.com", timeout=10)
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_deepseek_read_rejects_private_urls_before_any_request(monkeypatch):
    from agent_reach.runtime.result import ReachError

    calls = _deepseek(monkeypatch, {"title": "x", "content": "x"})

    with pytest.raises(ReachError) as exc:
        WebChannel()._deepseek_read(target="http://127.0.0.1/admin", timeout=10)
    assert exc.value.code == "BAD_INPUT"
    assert calls == []


@pytest.mark.parametrize("status,code,message", [
    (401, "NEED_SETUP", "rejected the API key"),
    (402, "UPSTREAM_BROKEN", "balance is insufficient"),
])
def test_deepseek_key_and_balance_errors_point_at_the_user(monkeypatch, status, code, message):
    import urllib.error

    from agent_reach.channels import web
    from agent_reach.runtime.result import ReachError

    def failing(url, **_):
        raise urllib.error.HTTPError(url, status, "error", {}, None)

    _deepseek(monkeypatch, {})
    monkeypatch.setattr(web, "http_request", failing)

    with pytest.raises(ReachError) as exc:
        WebChannel()._deepseek_search(query="q", limit=2, timeout=10)
    assert exc.value.code == code
    assert message in exc.value.message
    assert "agent-reach update" not in exc.value.hint
