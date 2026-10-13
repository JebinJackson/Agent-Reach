# Threads public channel

Requires desktop Chrome, OpenCLI >=1.8.8, the Browser Bridge extension, and a user-controlled browser session. Never export cookies or submit login credentials automatically.

Install the bundled read-only plugin with `agent-reach install --env=local --system --channels=threads`. This explicitly authorized installer also performs the usual core setup. For an existing installation, install the plugin alone: `opencli plugin install file:///ABSOLUTE_PATH/agent_reach/integrations/threads_opencli`.

Commands: `opencli threads search "query" --limit 10`, `opencli threads profile nasa --limit 3`, and `opencli threads read https://www.threads.com/@nasa/post/DeXEFulCqJa`. Search supports `--since YYYY-MM-DD` and `--until YYYY-MM-DD`, inclusive calendar days in Asia/Taipei. Unknown timestamps are excluded when filtering. Results are bounded and may be fewer than requested; no claim of exhaustive coverage.

Python: `get_channel('threads').search(query, limit=10, since=None, until=None)`. The same channel exposes profile and read. Doctor intentionally reports warn when the plugin is registered: login and target content require separate live verification.

Incremental collection: `python -m agent_reach.integrations.threads_research "query" --limit 10 --out ./results`. Uses a URL-keyed SQLite store, preserves first/last seen times, tracks updated payloads, and writes latest.json. An unchanged rerun does not add duplicate posts.

The adapter checks destination and query identity, retains posts while the page virtualizes, and uses bounded scrolling and navigation recovery. Text can contain UI labels or truncated first segments; media and complete reply threads are not transcribed. Live availability depends on site markup, browser bridge, account state and rate limits.
