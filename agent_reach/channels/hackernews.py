# -*- coding: utf-8 -*-
"""Hacker News — public API channel for stories, comments and search.

Why this channel exists
-----------------------
Every developer-facing platform in Agent Reach except GitHub needs *something*
before it works: Reddit's anonymous API is closed (login), Twitter needs a
cookie, Bilibili needs a CLI. Hacker News is the largest remaining
developer-news source with a **fully public, key-free, auth-free JSON API**, so
it belongs in the tier-0 "装好即用" group that the project's README leads with —
and it is the only zero-config way to answer "what is the developer world
talking about today".

Why the implementation looks like this
--------------------------------------
* *The API is id-list-first, item-per-request.* ``topstories.json`` returns 500
  ids and every item is its own request. Following the API's shape naively means
  one ``get_stories(limit=30)`` is 31 HTTP requests. Two things follow: the item
  fetches are **concurrent** (a bounded thread pool, stdlib only), and there is a
  **hard cap** on how many items one call may fetch, so a large ``limit`` cannot
  turn into a burst of hundreds of requests against a free public service.

* *Rank is restored from the id list, not from completion order.* A thread pool
  yields futures as they finish, so an ``as_completed`` loop hands back the "top
  stories" sorted by network latency. Rank is the entire meaning of that list, so
  results are re-ordered by the position of their id.

* *Dead items are collected in waves, not overscanned by a guess.*
  ``topstories.json`` routinely contains ids whose item is ``null`` (deleted),
  ``deleted: true`` or ``dead: true`` — flagged posts, moderator removals,
  self-deleted threads. Filtering after fetching exactly ``limit`` ids would make
  ``limit=30`` quietly return 25, and the caller would have no way to tell a short
  list from a short feed. A fixed overscan only moves that problem: the dead-item
  rate differs per feed and per day, so any constant is wrong on some feed. So
  ids are fetched in **waves** and the next wave is only started while the
  request is still short — which honours ``limit`` whenever enough ids are alive,
  and still respects a hard cap on total item requests, because a feed that is
  *entirely* dead must not become an unbounded number of requests.

* *Search is a second backend, and it is declared.* The Firebase API has no
  search endpoint at all. Algolia's HN Search API is public, key-free and the
  de-facto search for HN, so it is a declared second backend rather than a hidden
  extra host — ``check()`` probes both and says which one is down, because a
  channel that reports "ok" while half of it is unreachable is the kind of
  optimistic status this project's doctor exists to avoid.
"""

import json
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent_reach.utils.text import scrub_url_credentials

from .base import Channel

_UA = "agent-reach/1.0"
_TIMEOUT = 10
_MAX_RESPONSE_BYTES = 1024 * 1024

_FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0"
_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"

#: Hosts this channel will talk to. Anything else is refused before a socket is
#: opened, so a caller-supplied value can never redirect the channel elsewhere.
_FIREBASE_HOST = "hacker-news.firebaseio.com"
_ALGOLIA_HOST = "hn.algolia.com"

#: Feed name → Firebase endpoint stem. Named feeds rather than a raw endpoint
#: string so ``get_stories`` cannot be pointed at an arbitrary path.
FEEDS: Dict[str, str] = {
    "top": "topstories",
    "new": "newstories",
    "best": "beststories",
    "ask": "askstories",
    "show": "showstories",
    "job": "jobstories",
}

#: Upper bound on item requests for a single ``get_stories`` call, overscan
#: included. See the module docstring: without this, ``limit=500`` is 500
#: requests against a free public API.
MAX_ITEM_FETCHES = 80

#: Threads used for the item fan-out. Deliberately small: this is a courtesy to a
#: free public service, not a benchmark.
_ITEM_WORKERS = 8

#: Smallest wave of item ids to fetch. The wave is sized by how many stories are
#: still missing, so a wave of exactly the shortfall would make a feed with any
#: dead items cost one round trip per missing story. A floor of 10 keeps the
#: common case at one wave while staying well inside :data:`MAX_ITEM_FETCHES`.
_MIN_BATCH = 10


def _wave_size(missing: int, budget: int) -> int:
    """How many ids to fetch next: the shortfall, floored, capped by the budget."""
    return max(0, min(budget, max(missing, _MIN_BATCH)))


def _validate_api_url(url: str) -> None:
    """Allow only the two public HN HTTPS APIs."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid Hacker News API URL") from exc

    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in {_FIREBASE_HOST, _ALGOLIA_HOST}
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("only the public Hacker News HTTPS APIs are allowed")

    expected_prefix = "/v0/" if host == _FIREBASE_HOST else "/api/"
    if not parsed.path.startswith(expected_prefix):
        raise ValueError("only the public Hacker News HTTPS APIs are allowed")


def _get_json_with_urllib(url: str) -> Any:
    """Fetch bounded JSON with Python's standard HTTP stack."""
    _validate_api_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Hacker News API response exceeds the 1 MiB safety limit")
    return json.loads(raw.decode("utf-8"))


def _is_unexpected_tls_eof(error: BaseException) -> bool:
    """Return whether an exception chain contains the retryable TLS EOF.

    The same failure V2EX hits on some networks: the TLS record is cut before
    the close_notify, which ``urllib`` reports as ``SSLEOFError``. It is
    retryable; a certificate failure is not and must never be retried.
    """
    pending: List[BaseException] = [error]
    seen: set = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLError) and not isinstance(
            current, ssl.SSLCertVerificationError
        ):
            text = str(current).casefold()
            if (
                "unexpected_eof_while_reading" in text
                or "eof occurred in violation of protocol" in text
            ):
                return True
        for nested in (
            getattr(current, "reason", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _get_json(url: str) -> Any:
    """Fetch JSON once; a retryable TLS EOF is retried exactly once.

    A single retry rather than a loop: the failure is a network path resetting
    a record, not a transient server error, and retrying a public API
    indefinitely is how a client becomes the problem.
    """
    try:
        return _get_json_with_urllib(url)
    except Exception as exc:
        if isinstance(exc, ssl.SSLCertVerificationError):
            raise
        if not _is_unexpected_tls_eof(exc):
            raise
        return _get_json_with_urllib(url)


def _item_url(item_id: int) -> str:
    return f"{_FIREBASE_BASE}/item/{int(item_id)}.json"


def _feed_url(feed: str) -> str:
    stem = FEEDS.get(feed)
    if stem is None:
        raise ValueError(
            f"unknown Hacker News feed {feed!r}: expected one of {', '.join(sorted(FEEDS))}"
        )
    return f"{_FIREBASE_BASE}/{stem}.json"


# -------------------------------------------------------------------------- #
# normalisation
# -------------------------------------------------------------------------- #


def _is_gone(item: Any) -> bool:
    """Whether an item exists but must not be presented as content.

    Three distinct cases, and they are not interchangeable: ``None`` is an id
    that no longer resolves at all, ``deleted`` is author-removed, and ``dead``
    is flagged or moderator-removed. All three are absent from the API's
    id lists only *eventually*, so a client that trusts the id list will hit
    them.
    """
    if not isinstance(item, dict):
        return True
    return bool(item.get("deleted")) or bool(item.get("dead"))


def _normalise_story(item: Dict[str, Any]) -> Dict[str, Any]:
    """Render one story as the flat dict callers get.

    ``url`` is empty for Ask HN / text posts — they have no external target, and
    fabricating the discussion permalink into ``url`` would make the caller think
    there is an article to fetch. The permalink lives in ``hn_url`` instead.
    """
    item_id = item.get("id")
    return {
        "id": item_id,
        "title": item.get("title", "") or "",
        "url": item.get("url", "") or "",
        "hn_url": f"https://news.ycombinator.com/item?id={item_id}",
        "by": item.get("by", "") or "",
        "score": item.get("score", 0) or 0,
        "comments": item.get("descendants", 0) or 0,
        "time": item.get("time", 0) or 0,
        "type": item.get("type", "") or "",
        "text": _strip_html(item.get("text", "") or "")[:2000],
    }


def _strip_html(text: str) -> str:
    """Turn the HTML fragment HN returns for ``text`` into plain text.

    The API returns comment and post bodies as an HTML fragment (``<p>``,
    ``<a href="...">``, ``<i>``, ``&#x27;``). An agent reading raw markup spends
    its budget on tags, so the tags are dropped and the entities decoded — but
    link *targets* are kept in parentheses, because on HN the link is often the
    content and dropping it silently loses information.
    """
    import html
    import re

    with_targets = re.sub(
        r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2).strip() else m.group(1),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", with_targets)
    collapsed = re.sub(r"[ \t\r\f\v]+", " ", without_tags)
    collapsed = re.sub(r"\n\s*\n\s*", "\n\n", collapsed)
    return html.unescape(collapsed).strip()


def _fetch_items_concurrently(ids: Sequence[int]) -> Dict[int, Any]:
    """Fetch items in parallel and return ``{id: item}``.

    Failures are dropped rather than raised: one item that times out must not
    cost the caller the other 29, and the caller's contract is "as many stories
    as could be fetched", not "all or nothing". An id that never arrived is
    indistinguishable from a deleted one at this layer, and both are filtered by
    :func:`_is_gone`.
    """
    if not ids:
        return {}
    results: Dict[int, Any] = {}

    def fetch(item_id: int) -> Tuple[int, Optional[Any]]:
        try:
            return item_id, _get_json(_item_url(item_id))
        except Exception:
            return item_id, None

    with ThreadPoolExecutor(max_workers=min(_ITEM_WORKERS, len(ids))) as pool:
        for item_id, item in pool.map(fetch, ids):
            results[item_id] = item
    return results


class HackerNewsChannel(Channel):
    name = "hackernews"
    description = "Hacker News 热榜、帖子与评论"
    backends = ["HN Firebase API (public)", "Algolia HN Search (public)"]
    tier = 0

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return (
            host_matches(url, "news.ycombinator.com")
            or host_matches(url, _FIREBASE_HOST)
            or host_matches(url, _ALGOLIA_HOST)
        )

    # ------------------------------------------------------------------ #
    # Health check
    # ------------------------------------------------------------------ #

    def check(self, config=None):
        """Probe both backends and report the worst truthfully.

        The read backend is what makes this channel useful, so it decides
        ``ok`` vs ``warn``. The search backend is reported in the message rather
        than ignored: a doctor line that says ``ok`` while half the channel is
        unreachable is exactly the optimistic status this project avoids.
        """
        read_ok = True
        search_ok = True
        detail = ""
        try:
            _get_json(f"{_FIREBASE_BASE}/maxitem.json")
        except Exception as exc:
            read_ok = False
            detail = scrub_url_credentials(exc)

        try:
            _get_json(f"{_ALGOLIA_BASE}/search?query=test&tags=story&hitsPerPage=1")
        except Exception:
            search_ok = False

        if read_ok:
            self.active_backend = self.backends[0]
            if search_ok:
                return "ok", "公开 API 可用（热榜、帖子、评论、搜索，无需配置）"
            return "ok", "读取与评论可用；搜索后端（Algolia HN Search）当前不可达"
        if search_ok:
            self.active_backend = self.backends[1]
            return "warn", f"HN Firebase API 连接失败（可能需要代理）：{detail}"
        self.active_backend = None
        return "warn", f"HN API 连接失败（可能需要代理）：{detail}"

    # ------------------------------------------------------------------ #
    # Data-fetching methods
    # ------------------------------------------------------------------ #

    def get_stories(self, feed: str = "top", limit: int = 30) -> List[dict]:
        """获取一个 feed 的帖子列表。

        Args:
            feed:  one of ``top`` / ``new`` / ``best`` / ``ask`` / ``show`` / ``job``
            limit: maximum number of stories to return

        Returns:
            list of dicts with keys: id, title, url, hn_url, by, score,
            comments, time, type, text — in **feed rank order**, with deleted
            and flagged items removed.

        The result is collected in waves so ``limit`` is honoured whenever enough
        ids are alive, while total item requests stay under
        :data:`MAX_ITEM_FETCHES`. A short list therefore means the feed really is
        short, not that the filter ate it.
        """
        if limit <= 0:
            return []

        ids = _get_json(_feed_url(feed))
        if not isinstance(ids, list):
            raise ValueError("Hacker News feed did not return an id list")

        wanted = min(int(limit), MAX_ITEM_FETCHES)
        budget = MAX_ITEM_FETCHES
        cursor = 0
        stories: List[dict] = []

        while len(stories) < wanted and cursor < len(ids) and budget > 0:
            size = _wave_size(wanted - len(stories), budget)
            if size <= 0:
                break
            wave = ids[cursor : cursor + size]
            cursor += len(wave)
            budget -= len(wave)
            fetched = _fetch_items_concurrently(wave)
            # Rank order comes from the id list, never from the fetch order: a
            # thread pool finishes in latency order, and "top stories" sorted by
            # latency is not a top-stories list.
            for item_id in wave:
                item = fetched.get(item_id)
                if _is_gone(item):
                    continue
                if item.get("type") == "comment":
                    continue
                stories.append(_normalise_story(item))
                if len(stories) >= wanted:
                    break
        return stories

    def get_item(self, item_id: int) -> dict:
        """获取单个条目的完整信息（帖子或评论）。

        Returns:
            dict with the same keys as :meth:`get_stories`, plus ``kids`` (the
            ids of direct replies — the API returns ids, not comments).
        """
        item = _get_json(_item_url(item_id))
        if _is_gone(item):
            raise ValueError(f"Hacker News item {item_id} does not exist (deleted or removed)")
        payload = _normalise_story(item)
        payload["kids"] = list(item.get("kids", []) or [])
        payload["parent"] = item.get("parent")
        return payload

    def get_comments(self, item_id: int, limit: int = 20) -> List[dict]:
        """获取某个帖子的顶层评论。

        Only the top level: replies carry their own ``kids``, so following the
        tree is the caller's decision (and its own budget problem) rather than a
        depth this method picks for them.
        """
        if limit <= 0:
            return []
        parent = self.get_item(item_id)
        kids = parent.get("kids", [])[: max(1, min(int(limit), MAX_ITEM_FETCHES))]
        fetched = _fetch_items_concurrently(kids)
        comments: List[dict] = []
        for kid in kids:
            item = fetched.get(kid)
            if _is_gone(item):
                continue
            comments.append(
                {
                    "id": item.get("id"),
                    "by": item.get("by", "") or "",
                    "time": item.get("time", 0) or 0,
                    "text": _strip_html(item.get("text", "") or ""),
                    "replies": len(item.get("kids", []) or []),
                }
            )
        return comments

    def get_user(self, username: str) -> dict:
        """获取用户公开资料（karma、注册时间、简介）。"""
        url = f"{_FIREBASE_BASE}/user/{urllib.parse.quote(str(username), safe='')}.json"
        data = _get_json(url)
        if not isinstance(data, dict):
            raise ValueError(f"Hacker News user {username!r} does not exist")
        return {
            "id": data.get("id", ""),
            "karma": data.get("karma", 0) or 0,
            "created": data.get("created", 0) or 0,
            "about": _strip_html(data.get("about", "") or ""),
            "submitted_count": len(data.get("submitted", []) or []),
        }

    def read(self, url: str) -> str:
        """Read a Hacker News discussion as plain text.

        Accepts ``https://news.ycombinator.com/item?id=<id>``. Returns the story
        header plus its top-level comments, which is what an agent asked to
        "read this HN thread" actually needs.
        """
        item_id = _item_id_from_url(url)
        if item_id is None:
            raise ValueError(
                "not a Hacker News item URL: expected "
                "https://news.ycombinator.com/item?id=<id>"
            )
        story = self.get_item(item_id)
        lines = [story["title"] or f"Hacker News item {item_id}"]
        if story["url"]:
            lines.append(story["url"])
        lines.append(
            f"{story['score']} points by {story['by'] or 'unknown'} · "
            f"{story['comments']} comments · {story['hn_url']}"
        )
        if story["text"]:
            lines.extend(["", story["text"]])
        comments = self.get_comments(item_id, limit=20)
        if comments:
            lines.extend(["", f"--- top {len(comments)} comments ---"])
            for comment in comments:
                lines.extend(["", f"{comment['by']}: {comment['text']}"])
        return "\n".join(lines)

    def search(self, query: str, limit: int = 10) -> List[dict]:
        """通过 Algolia HN Search 搜索帖子。

        The Firebase API has no search endpoint; this is the public, key-free
        search HN's own site uses. It is declared as this channel's second
        backend rather than hidden, so ``doctor`` can report it going down.

        Returns:
            list of dicts with keys: title, url, hn_url, points, comments,
            author, created_at
        """
        if limit <= 0:
            return []
        params = urllib.parse.urlencode(
            {
                "query": str(query),
                "tags": "story",
                "hitsPerPage": min(int(limit), 100),
            }
        )
        data = _get_json(f"{_ALGOLIA_BASE}/search?{params}")
        hits = data.get("hits", []) if isinstance(data, dict) else []
        results = []
        for hit in hits:
            object_id = hit.get("objectID")
            results.append(
                {
                    "title": hit.get("title") or hit.get("story_title") or "",
                    "url": hit.get("url") or "",
                    "hn_url": f"https://news.ycombinator.com/item?id={object_id}",
                    "points": hit.get("points") or 0,
                    "comments": hit.get("num_comments") or 0,
                    "author": hit.get("author") or "",
                    "created_at": hit.get("created_at") or "",
                }
            )
        return results


def _item_id_from_url(url: str) -> Optional[int]:
    """Extract the item id from an HN discussion URL, or ``None``.

    Only ``news.ycombinator.com/item?id=<digits>`` is accepted. Parsing the id
    out of an arbitrary query string would let ``.../item?id=1&id=2`` and
    ``.../item?id=1e3`` through, and the value ends up in a URL path.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url))
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in {"news.ycombinator.com", "hn.algolia.com"}:
        return None
    if parsed.path.rstrip("/") not in {"/item", "/api/v1/items"}:
        return None
    values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True).get("id") or []
    if len(values) != 1:
        return None
    raw = values[0]
    if not raw.isdigit():
        return None
    return int(raw)
