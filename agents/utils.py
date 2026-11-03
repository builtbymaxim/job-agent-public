"""Shared utilities for Gemini API calls — retry logic for 429/503 errors."""

from __future__ import annotations

import logging
import re
import time

from google.genai import errors, types

from agents.usage_tracker import usage_tracker

logger = logging.getLogger(__name__)


# Seconds between calls. On the free tier this kept us under ~10 RPM; with billing
# enabled it can be lowered (config `gemini.rpm_delay`) to speed runs up sharply.
_rpm_delay = 7.0


def set_rpm_delay(seconds: float) -> None:
    """Set the inter-call delay (called once from config at pipeline init)."""
    global _rpm_delay
    _rpm_delay = max(0.0, float(seconds))


class QuotaExhaustedError(RuntimeError):
    """A daily/project quota is exhausted — retrying within the run is futile."""


class RateLimitedError(RuntimeError):
    """Transient rate limiting (429/503) that survived all retries."""


def _is_daily_quota(error_str: str) -> bool:
    """True if a 429 is a per-day / project quota cap (vs a transient per-minute hit)."""
    return "perday" in error_str.lower()


def generate_with_retry(client, model: str, contents: str, config=None, max_retries: int = 5):
    """Wrap generate_content with retry for transient 429/503.

    Raises `QuotaExhaustedError` immediately on a daily/project-quota 429 (no point
    waiting), and `RateLimitedError` if transient limits survive all retries.
    """
    if _rpm_delay:
        time.sleep(_rpm_delay)
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model, contents=contents, config=config
            )
            usage_tracker.record(model, getattr(response, "usage_metadata", None))
            return response
        except errors.ServerError:
            # 503 — temporary overload
            if attempt < max_retries - 1:
                wait = 15 * (attempt + 1)
                logger.warning("503 overload, retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            raise RateLimitedError("Gemini overloaded (503) after retries")
        except errors.ClientError as e:
            s = str(e)
            if "429" in s:
                if _is_daily_quota(s):
                    raise QuotaExhaustedError(
                        "Daily/project Gemini quota exhausted — enable billing or wait for reset."
                    ) from e
                if attempt < max_retries - 1:
                    delay = _parse_retry_delay(s)
                    logger.warning("Rate limited (429), waiting %ds (attempt %d/%d)", delay, attempt + 1, max_retries)
                    time.sleep(delay)
                    continue
                raise RateLimitedError("Gemini rate limit (429) after retries") from e
            raise


def _parse_retry_delay(error_str: str) -> int:
    """Extract retryDelay seconds from a 429 error message, default 65s."""
    match = re.search(r"retryDelay.*?(\d+)s", error_str)
    if match:
        return int(match.group(1)) + 5  # add 5s buffer
    return 65
