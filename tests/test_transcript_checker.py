from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.transcript_checker import check_transcript_truncation, parse_transcript


class TestTranscriptParser:
    def test_parse_valid_jsonl(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write('{"type":"test","content":"hello"}\n')
            fh.write('{"type":"test","content":"world"}\n')
            path = fh.name
        try:
            events = parse_transcript(path)
            assert len(events) == 2
            assert events[0]["type"] == "test"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_parse_malformed_jsonl(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write('{"type":"test","content":"hello"}\n')
            fh.write('not json at all\n')
            fh.write('{"type":"test","content":"world"}\n')
            path = fh.name
        try:
            events = parse_transcript(path)
            assert len(events) == 3
            assert events[1].get("_malformed") is True
        finally:
            Path(path).unlink(missing_ok=True)

    def test_parse_empty_transcript(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            path = fh.name
        try:
            events = parse_transcript(path)
            assert events == []
        finally:
            Path(path).unlink(missing_ok=True)


class TestTranscriptTruncation:
    def test_detects_truncation_in_tool_output(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({
                "type": "tool_result",
                "role": "tool",
                "tool_name": "shell",
                "command": "grep",
                "stdout": "Results:\n... truncated ...\nshowing first 50",
            }) + "\n")
            path = fh.name
        try:
            result = check_transcript_truncation(path)
            assert result.status == "blocked"
            assert len(result.issues) >= 1
            assert any("truncated" in i.message.lower() for i in result.issues)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_passes_clean_transcript(self, tmp_transcript):
        result = check_transcript_truncation(tmp_transcript)
        assert result.status == "passed"

    def test_ignores_user_discussion_of_truncated(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({
                "type": "message",
                "role": "user",
                "content": "The output was truncated so I need you to re-run it.",
            }) + "\n")
            path = fh.name
        try:
            result = check_transcript_truncation(path)
            assert result.status == "passed"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_handles_missing_transcript(self):
        result = check_transcript_truncation("/nonexistent/path.jsonl")
        assert result.status == "blocked"
        assert any("empty" in i.message.lower() for i in result.issues)
