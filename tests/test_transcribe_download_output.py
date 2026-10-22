"""Select extracted audio rather than yt-dlp's optional sidecar files."""

import json
import shutil
import wave
from pathlib import Path

import pytest

from agent_reach import transcribe as tr


def report_output(cmd, audio):
    if "--print-to-file" in cmd:
        index = cmd.index("--print-to-file")
        assert cmd[index + 1] == "after_move:%(filepath)j"
        Path(cmd[index + 2]).write_text(json.dumps(str(audio)), encoding="utf-8")


@pytest.mark.parametrize(
    "sidecar", ["source.info.json", "source.jpg", "source.en.vtt", "source.description"]
)
def test_download_selects_audio_when_sidecars_are_present(tmp_path, monkeypatch, sidecar):
    def write_download(_cmd, timeout):
        (tmp_path / "source.m4a").write_bytes(b"audio")
        (tmp_path / sidecar).write_bytes(b"metadata")
        report_output(_cmd, tmp_path / "source.m4a")

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", write_download)

    audio = tr.download_audio("https://example.com/audio", tmp_path)

    assert audio == tmp_path / "source.m4a"
    assert audio.read_bytes() == b"audio"
    assert (tmp_path / sidecar).read_bytes() == b"metadata"


def test_download_uses_reported_postprocessed_path(tmp_path, monkeypatch):
    audio = tmp_path / "renamed audio.m4a"

    def write_download(cmd, timeout):
        audio.write_bytes(b"audio")
        (tmp_path / "source.info.json").write_text("{}", encoding="utf-8")
        report_output(cmd, audio)

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", write_download)

    assert tr.download_audio("https://example.com/audio", tmp_path) == audio


def test_download_rejects_sidecar_only_output(tmp_path, monkeypatch):
    def write_metadata(_cmd, timeout):
        # --write-info-json can run even when the media download is skipped.
        (tmp_path / "source.info.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", write_metadata)

    with pytest.raises(tr.TranscribeError, match="no output file"):
        tr.download_audio("https://example.com/audio", tmp_path)


@pytest.mark.parametrize("content", ["not json", "null", "{}"])
def test_download_rejects_invalid_report_and_cleans_up(tmp_path, monkeypatch, content):
    reports = []

    def write_invalid_report(cmd, timeout):
        report = Path(cmd[cmd.index("--print-to-file") + 2])
        report.write_text(content, encoding="utf-8")
        reports.append(report)

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", write_invalid_report)

    with pytest.raises(tr.TranscribeError, match="invalid output path"):
        tr.download_audio("https://example.com/audio", tmp_path)

    assert len(reports) == 1
    assert not reports[0].parent.exists()


def test_download_rejects_missing_reported_media(tmp_path, monkeypatch):
    def write_report(cmd, timeout):
        (tmp_path / "source.info.json").write_text("{}", encoding="utf-8")
        report_output(cmd, tmp_path / "source.m4a")

    monkeypatch.setattr(tr, "_require", lambda _binary: None)
    monkeypatch.setattr(tr, "_run", write_report)

    with pytest.raises(tr.TranscribeError, match="no output file"):
        tr.download_audio("https://example.com/audio", tmp_path)


def test_download_selects_native_ytdlp_audio_with_metadata(tmp_path, monkeypatch):
    """Run real yt-dlp and FFmpeg against generated local audio, never a service."""
    for binary in ("yt-dlp", "ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            pytest.skip(f"native {binary} is required")

    source = tmp_path / "fixture.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 8000)
    out_dir = tmp_path / "download"
    out_dir.mkdir()
    run = tr._run

    def download_local_fixture(cmd, timeout):
        assert cmd[-2:] == ["--", "https://example.com/audio"]
        # Substitute only the external source. Keep the production download
        # options and execute the installed tools with isolated configuration.
        run(
            [
                cmd[0],
                "--ignore-config",
                "--no-update",
                "--enable-file-urls",
                "--write-info-json",
                *cmd[1:-1],
                source.as_uri(),
            ],
            timeout=30,
        )

    monkeypatch.setattr(tr, "_run", download_local_fixture)

    audio = tr.download_audio("https://example.com/audio", out_dir)

    assert (out_dir / "source.info.json").is_file()
    assert audio == out_dir / "source.m4a"
    assert tr._probe_audio_duration(audio) == pytest.approx(1.0, abs=0.1)
    assert source.is_file()
