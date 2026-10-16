"""Run the bundled RSS example with a different Python on PATH."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="The documented example uses a POSIX shell")
@pytest.mark.parametrize("isolated", [False, True])
def test_rss_example_uses_app_interpreter(tmp_path, isolated):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        '<rss version="2.0"><channel><title>Fixture</title>'
        "<link>https://example.test</link><description>offline</description>"
        "<item><title>One</title><link>https://example.test/one</link></item>"
        "</channel></rss>"
    )
    # Expose the app separately from PATH's Python, as an isolated install does.
    app = tmp_path / "agent-reach"
    app.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + ' -c "from agent_reach.cli import main; main()" "$@"\n'
    )
    app.chmod(0o755)
    python = tmp_path / "python3"
    if isolated:
        python.write_text('#!/bin/sh\necho "No module named feedparser" >&2\nexit 1\n')
        python.chmod(0o755)
    else:
        python.symlink_to(sys.executable)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"])
    from agent_reach.channels.rss import RSSChannel

    assert RSSChannel().check()[0] == "ok"
    reference = Path(__file__).resolve().parents[1] / "agent_reach/skill/references/web.md"
    rss = reference.read_text().split("## RSS (feedparser)", 1)[1]
    command = re.search(r"```[^\n]*\n(.*?)```", rss, re.S).group(1)
    result = subprocess.run(
        ["/bin/sh", "-c", command.replace("FEED_URL", str(feed))],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "One — https://example.test/one"


def test_python_path_reports_running_interpreter():
    result = subprocess.run(
        [sys.executable, "-c", "from agent_reach.cli import main; main()", "python-path"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.executable
