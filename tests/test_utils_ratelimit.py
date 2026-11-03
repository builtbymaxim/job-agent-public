"""Tests for rate-limit handling helpers in agents.utils."""

import agents.utils as u
from agents.utils import (
    QuotaExhaustedError,
    RateLimitedError,
    _is_daily_quota,
    set_rpm_delay,
)


def test_daily_quota_detection():
    daily = "429 RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProjectPerModel-FreeTier ..."
    transient = "429 Too Many Requests; PerMinute limit, retryDelay 30s"
    assert _is_daily_quota(daily) is True
    assert _is_daily_quota(transient) is False


def test_set_rpm_delay_clamps():
    set_rpm_delay(0.5)
    assert u._rpm_delay == 0.5
    set_rpm_delay(-3)
    assert u._rpm_delay == 0.0
    set_rpm_delay(1)  # restore a sane value


def test_error_types_are_distinct():
    assert issubclass(QuotaExhaustedError, RuntimeError)
    assert issubclass(RateLimitedError, RuntimeError)
    assert not issubclass(QuotaExhaustedError, RateLimitedError)
