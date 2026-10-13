from unittest.mock import patch

import pytest

from agent_reach.channels import get_channel
from agent_reach.channels.threads import ThreadsChannel


def test_registered_hosts():
    ch = get_channel("threads")
    assert isinstance(ch, ThreadsChannel)
    assert ch.can_handle("https://www.threads.com/@nasa/post/ABC")
    assert not ch.can_handle("https://threads.com.evil.test/@nasa/post/ABC")


@pytest.mark.parametrize(
    "url",
    [
        "http://www.threads.com/@nasa/post/ABC",
        "https://www.threads.com/messages",
        "https://user:secret@www.threads.com/@nasa/post/ABC",
        "https://www.threads.com/@nasa/post/ABC?token=x",
    ],
)
def test_invalid_urls_never_execute(url):
    with patch.object(ThreadsChannel, "_run") as run:
        with pytest.raises(ValueError):
            ThreadsChannel().read(url)
        run.assert_not_called()


def test_dates_validated_before_backend():
    with patch.object(ThreadsChannel, "_run") as run:
        with pytest.raises(ValueError):
            ThreadsChannel().search("test", 10, "2026-02-31")
        with pytest.raises(ValueError):
            ThreadsChannel().search("test", 10, "2026-10-13", "2026-10-01")
        run.assert_not_called()


def test_incremental_storage(tmp_path):
    from agent_reach.integrations.threads_research import persist

    rows = [{"url": "https://www.threads.com/@nasa/post/ABC", "text": "public"}]
    assert persist(rows, tmp_path, "test")["new"] == 1
    assert persist(rows, tmp_path, "test")["new"] == 0
    rows[0]["text"] = "updated"
    assert persist(rows, tmp_path, "test")["updated"] == 1
    # On Windows a leaked SQLite connection prevents removing or moving the file.
    (tmp_path / "posts.sqlite3").rename(tmp_path / "closed.sqlite3")


def test_missing_backend(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert ThreadsChannel().check()[0] == "off"
