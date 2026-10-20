"""CLI tests for `agent-reach xhs-enrich` (no network, no Chrome)."""

import json
from unittest.mock import patch

import pytest

import agent_reach.cli as cli
from agent_reach.cli import main
from agent_reach.xhs_multimodal import XhsMultimodalError

NOTE_ROWS = [{"field": "title", "value": "测试"}]
NOTE_URL = "https://www.xiaohongshu.com/explore/abc"


def test_json_input_skips_opencli(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_run_opencli", lambda *a, **k: pytest.fail("no CLI"))
    note = tmp_path / "note.json"
    note.write_text(json.dumps(NOTE_ROWS, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "enriched.json"
    with patch("sys.argv", ["agent-reach", "xhs-enrich", str(note), "-o", str(out)]):
        main()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["title"] == "测试"
    assert data["multimodal"] == {"images": [], "video": None}


def test_skips_media_download_when_disabled(monkeypatch, capsys):
    calls = []

    def fake(args, timeout):
        calls.append(args)
        return NOTE_ROWS

    monkeypatch.setattr(cli, "_run_opencli", fake)
    argv = ["agent-reach", "xhs-enrich", NOTE_URL, "--no-ocr", "--no-transcribe"]
    with patch("sys.argv", argv):
        main()
    assert [args[1] for args in calls] == ["note"]
    assert "multimodal" in capsys.readouterr().out


def test_opencli_entry_reads_package_main(tmp_path):
    package = tmp_path / "node_modules" / "@jackwener" / "opencli"
    (package / "dist").mkdir(parents=True)
    (package / "dist" / "main.js").write_text("", encoding="utf-8")
    (package / "package.json").write_text('{"main": "dist/main.js"}', encoding="utf-8")
    assert cli._opencli_entry(package) == package / "dist" / "main.js"
    assert cli._opencli_entry(tmp_path / "missing") is None


def test_media_download_failure_keeps_note(monkeypatch, capsys):
    def fake(args, timeout):
        if args[1] == "download":
            raise XhsMultimodalError("download failed")
        return NOTE_ROWS

    monkeypatch.setattr(cli, "_run_opencli", fake)
    argv = ["agent-reach", "xhs-enrich", NOTE_URL, "--no-transcribe"]
    with patch("sys.argv", argv):
        main()
    out = capsys.readouterr().out
    assert "download failed" in out
    assert "测试" in out
