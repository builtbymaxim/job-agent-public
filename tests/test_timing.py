"""Tests for the run-timing helpers."""

import time

from agents.timing import StageTimer, human_duration


def test_human_duration():
    assert human_duration(0) == "0s"
    assert human_duration(45) == "45s"
    assert human_duration(192) == "3m 12s"
    assert human_duration(3840) == "1h 04m"


def test_stage_accumulates():
    t = StageTimer()
    with t.stage("a"):
        time.sleep(0.01)
    with t.stage("a"):
        time.sleep(0.01)
    with t.stage("b"):
        time.sleep(0.01)
    assert set(t.stages) == {"a", "b"}
    assert t.stages["a"] >= t.stages["b"]   # 'a' ran twice
    assert t.total_seconds >= t.stages["a"] + t.stages["b"]


def test_breakdown_sorted_slowest_first():
    t = StageTimer()
    t.stages = {"fast": 1.0, "slow": 5.0, "mid": 3.0}
    labels = [label for label, _ in t.breakdown()]
    assert labels == ["slow", "mid", "fast"]
