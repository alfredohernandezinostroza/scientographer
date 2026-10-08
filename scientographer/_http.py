# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Polite HTTP for the stages that call public APIs (PubMed, OpenAlex): a rate
limit, retries with backoff on network errors and 429/5xx answers, and errors
that say what the server answered. Standard library only."""

import json
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from scientographer import __version__

logger = logging.getLogger(__name__)

USER_AGENT = f"scientographer/{__version__} (+https://github.com/alfredohernandezinostroza/scientographer)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class RateLimiter:
    """At most `per_second` acquisitions per second, across threads."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / max(per_second, 1e-9)
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            wait = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if wait > 0:
            time.sleep(wait)


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} from {url.split('?')[0]}: {body[:300]}")
        self.status = status
        self.body = body


def request(url: str, params: dict | None = None, data: dict | None = None,
            limiter: RateLimiter | None = None, retries: int = 6, timeout: float = 120,
            headers: dict | None = None, give_up_on=()) -> str:
    """GET `url` with `params` (or POST `data` form-encoded) and return the body.
    Retries network errors and 429/5xx with exponential backoff (honouring
    Retry-After); a status in `give_up_on` raises HttpError at once."""
    full = url + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    last: Exception | None = None
    for attempt in range(retries + 1):
        if limiter:
            limiter.acquire()
        req = urllib.request.Request(full, data=body, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")
            last = HttpError(err.code, full, text)
            if err.code in give_up_on or err.code not in RETRY_STATUSES:
                raise last from None
            delay = _retry_after(err) or min(120.0, 2.0 ** attempt + random.random())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = err
            delay = min(120.0, 2.0 ** attempt + random.random())
        if attempt < retries:
            logger.warning("request failed (%s); retrying in %.0f s", last, delay)
            time.sleep(delay)
    raise last  # type: ignore[misc]


def request_json(url: str, **kwargs) -> dict:
    return json.loads(request(url, **kwargs))


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    value = err.headers.get("Retry-After") if err.headers else None
    try:
        return min(300.0, float(value)) if value else None
    except ValueError:
        return None
