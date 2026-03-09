"""Tests for SignalSource — FFmpeg stderr parsing and signal extraction."""

from __future__ import annotations

import pytest

from commercial_detector.models import DetectionSignal, SignalType
from commercial_detector.signal_source import parse_ebur128_line, parse_stderr_line


class TestSilenceDetectParsing:
    """Parse silencedetect filter output from FFmpeg stderr."""

    def test_silence_start(self):
        line = "[silencedetect @ 0x7f8b4c000b80] silence_start: 5.47"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.signal_type == SignalType.SILENCE_START
        assert signal.timestamp == pytest.approx(5.47)
        assert signal.value == 0.0

    def test_silence_end_with_duration(self):
        line = "[silencedetect @ 0x7f8b4c000b80] silence_end: 5.77 | silence_duration: 0.3"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.signal_type == SignalType.SILENCE_END
        assert signal.timestamp == pytest.approx(5.77)
        assert signal.value == pytest.approx(0.3)  # duration

    def test_silence_start_integer_timestamp(self):
        line = "[silencedetect @ 0xabc] silence_start: 120"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.timestamp == pytest.approx(120.0)

    def test_silence_end_long_duration(self):
        line = "[silencedetect @ 0xabc] silence_end: 347.82 | silence_duration: 2.15"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.timestamp == pytest.approx(347.82)
        assert signal.value == pytest.approx(2.15)


class TestBlackDetectParsing:
    """Parse blackdetect filter output from FFmpeg stderr."""

    def test_black_detect_event(self):
        line = "[blackdetect @ 0x7f8b4c001a40] black_start:5.47 black_end:5.52 black_duration:0.05"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.signal_type == SignalType.BLACK_START
        assert signal.timestamp == pytest.approx(5.47)
        assert signal.value == pytest.approx(0.05)  # duration

    def test_black_detect_with_spaces(self):
        line = "[blackdetect @ 0xabc] black_start: 100.5 black_end: 101.0 black_duration: 0.5"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.timestamp == pytest.approx(100.5)
        assert signal.value == pytest.approx(0.5)

    def test_long_black_duration(self):
        line = "[blackdetect @ 0xabc] black_start:0.0 black_end:3.5 black_duration:3.5"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.timestamp == pytest.approx(0.0)
        assert signal.value == pytest.approx(3.5)


class TestSceneChangeParsing:
    """Parse scdet filter output from FFmpeg stderr."""

    def test_scene_change_equals_format(self):
        line = "[Parsed_scdet_1 @ 0xabc] lavfi.scd.score=42.35 lavfi.scd.time=12.50"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.signal_type == SignalType.SCENE_CHANGE
        assert signal.timestamp == pytest.approx(12.50)
        assert signal.value == pytest.approx(42.35)  # score

    def test_scene_change_colon_format(self):
        line = "[scdet @ 0xabc] lavfi.scd.score: 85.2, lavfi.scd.time: 300.0"
        signal = parse_stderr_line(line)
        assert signal is not None
        assert signal.signal_type == SignalType.SCENE_CHANGE
        assert signal.timestamp == pytest.approx(300.0)
        assert signal.value == pytest.approx(85.2)


class TestUnrecognizedLines:
    """Lines that aren't filter events should return None."""

    def test_progress_line(self):
        assert parse_stderr_line("frame= 1234 fps= 30 q=-0.0 size=N/A") is None

    def test_empty_line(self):
        assert parse_stderr_line("") is None

    def test_generic_warning(self):
        assert parse_stderr_line("[mp4 @ 0xabc] moov atom not found") is None

    def test_ffmpeg_version_line(self):
        assert parse_stderr_line("ffmpeg version 6.1 Copyright (c) 2000-2024") is None

    def test_input_line(self):
        assert parse_stderr_line("Input #0, matroska,webm, from 'test.webm':") is None


class TestEbur128Parsing:
    """Parse ebur128 loudness measurement lines from FFmpeg stderr."""

    def test_standard_ebur128_line(self):
        line = (
            "[Parsed_ebur128_1 @ 0x7f8b4c000b80] t: 1.2    "
            "TARGET:-23 LUFS    M: -22.3 S: -21.8    I: -24.0 LUFS    LRA: 6.2 LU"
        )
        result = parse_ebur128_line(line)
        assert result is not None
        timestamp, momentary, short_term = result
        assert timestamp == pytest.approx(1.2)
        assert momentary == pytest.approx(-22.3)
        assert short_term == pytest.approx(-21.8)

    def test_ebur128_line_at_zero(self):
        line = (
            "[Parsed_ebur128_1 @ 0xabc] t: 0.0    "
            "TARGET:-23 LUFS    M: -70.0 S: -70.0    I: -70.0 LUFS    LRA: 0.0 LU"
        )
        result = parse_ebur128_line(line)
        assert result is not None
        assert result[0] == pytest.approx(0.0)

    def test_non_ebur128_line_returns_none(self):
        assert parse_ebur128_line("frame= 1234 fps= 30 q=-0.0 size=N/A") is None

    def test_silence_detect_line_not_matched(self):
        line = "[silencedetect @ 0x7f8b4c000b80] silence_start: 5.47"
        assert parse_ebur128_line(line) is None

    def test_ebur128_with_high_timestamp(self):
        line = (
            "[Parsed_ebur128_2 @ 0xdef] t: 1234.5    "
            "TARGET:-23 LUFS    M: -18.0 S: -19.5    I: -22.0 LUFS    LRA: 8.5 LU"
        )
        result = parse_ebur128_line(line)
        assert result is not None
        assert result[0] == pytest.approx(1234.5)
        assert result[1] == pytest.approx(-18.0)
        assert result[2] == pytest.approx(-19.5)


class TestSignalDataclass:
    """Verify DetectionSignal is a proper frozen dataclass."""

    def test_signal_immutable(self):
        signal = DetectionSignal(
            timestamp=1.0,
            signal_type=SignalType.SILENCE_START,
        )
        with pytest.raises(AttributeError):
            signal.timestamp = 2.0  # type: ignore[misc]

    def test_signal_default_value(self):
        signal = DetectionSignal(
            timestamp=1.0,
            signal_type=SignalType.SILENCE_START,
        )
        assert signal.value == 0.0
