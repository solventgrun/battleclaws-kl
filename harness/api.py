"""Thin BattleClaws HTTP client.

One client instance per creature (per API key). Provides:
    - centralized requests.Session with auth header
    - client-side rate limiting (60 req/min global, 10 writes/min per key)
    - retries with exponential backoff on 429 and 5xx, honoring
      Retry-After and X-RateLimit-Reset headers when present
    - structured JSONL wire logging of every request/response

Endpoint payload shapes follow docs/battleclaws-skill.md. Two endpoints
(challenges and challenge responses) have undocumented request bodies in
the skill file; the shapes used here are best guesses and are flagged in
their docstrings. Verify them once API keys exist.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

# Platform limits from docs/battleclaws-skill.md
GLOBAL_REQS_PER_MIN = 60
WRITES_PER_MIN = 10

MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0
WIRE_LOG_BODY_LIMIT = 4000


class BattleClawsError(Exception):
    """Raised when a request fails after retries or with a client error."""

    def __init__(self, message: str, status: Optional[int] = None,
                 code: Optional[str] = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


class RateLimiter:
    """Sliding-window rate limiter for one API key.

    Enforces a global requests-per-minute window and a stricter
    writes-per-minute window. Thread-safe; acquire() blocks until the
    request may proceed.
    """

    def __init__(self, global_per_min: int = GLOBAL_REQS_PER_MIN,
                 writes_per_min: int = WRITES_PER_MIN,
                 window_s: float = 60.0):
        self._lock = threading.Lock()
        self._window_s = window_s
        self._global_limit = global_per_min
        self._write_limit = writes_per_min
        self._global_times: deque = deque()
        self._write_times: deque = deque()

    def _prune(self, dq: deque, now: float) -> None:
        while dq and now - dq[0] >= self._window_s:
            dq.popleft()

    def _wait_needed(self, now: float, write: bool) -> float:
        self._prune(self._global_times, now)
        self._prune(self._write_times, now)
        wait = 0.0
        if len(self._global_times) >= self._global_limit:
            wait = max(wait, self._window_s - (now - self._global_times[0]))
        if write and len(self._write_times) >= self._write_limit:
            wait = max(wait, self._window_s - (now - self._write_times[0]))
        return wait

    def acquire(self, write: bool = False) -> None:
        """Block until a request slot is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                wait = self._wait_needed(now, write)
                if wait <= 0:
                    self._global_times.append(now)
                    if write:
                        self._write_times.append(now)
                    return
            log.debug("Rate limiter sleeping %.2fs (write=%s)", wait, write)
            time.sleep(wait + 0.05)


def _retry_delay(attempt: int, response: Optional[requests.Response]) -> float:
    """Compute backoff delay, honoring server rate-limit headers if present."""
    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** attempt))
    delay += random.uniform(0, 0.5)
    if response is None:
        return delay
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    reset = response.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            val = float(reset)
            # Header may be epoch seconds or seconds-until-reset.
            if val > 1e6:
                val = max(0.0, val - time.time())
            delay = max(delay, min(val, BACKOFF_CAP_S * 2))
        except ValueError:
            pass
    return delay


class BattleClawsClient:
    """HTTP client for the BattleClaws API, one instance per creature."""

    def __init__(self, api_base: str, api_key: Optional[str] = None,
                 handle: str = "anon", wire_log_dir: Optional[Path] = None,
                 timeout_s: float = 30.0):
        self.api_base = api_base.rstrip("/")
        self.handle = handle
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"
        self._limiter = RateLimiter()
        self._wire_lock = threading.Lock()
        self._wire_path: Optional[Path] = None
        if wire_log_dir is not None:
            wire_log_dir.mkdir(parents=True, exist_ok=True)
            self._wire_path = wire_log_dir / f"{handle}.jsonl"

    # ---------------------------------------------------------------- core

    def _wire_log(self, record: dict) -> None:
        if self._wire_path is None:
            return
        with self._wire_lock:
            with self._wire_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _truncate(value: Any) -> Any:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > WIRE_LOG_BODY_LIMIT:
            return text[:WIRE_LOG_BODY_LIMIT] + "...[truncated]"
        return value

    def request(self, method: str, path: str, body: Optional[dict] = None,
                write: Optional[bool] = None) -> dict:
        """Issue one API request with rate limiting, retries, and wire logging.

        Returns the parsed JSON response body. Raises BattleClawsError on
        non-retryable client errors or after exhausting retries.
        """
        if write is None:
            write = method.upper() != "GET"
        url = self.api_base + path
        last_exc: Optional[Exception] = None
        response: Optional[requests.Response] = None

        for attempt in range(MAX_ATTEMPTS):
            self._limiter.acquire(write=write)
            started = time.monotonic()
            try:
                response = self._session.request(
                    method, url, json=body, timeout=self.timeout_s)
            except requests.RequestException as exc:
                last_exc = exc
                response = None
                log.warning("%s %s network error (attempt %d): %s",
                            method, path, attempt + 1, exc)
                self._wire_log({
                    "ts": time.time(), "handle": self.handle,
                    "method": method, "path": path, "attempt": attempt + 1,
                    "status": None, "error": str(exc),
                })
                time.sleep(_retry_delay(attempt, None))
                continue

            latency_ms = (time.monotonic() - started) * 1000.0
            try:
                parsed: Any = response.json()
            except ValueError:
                parsed = {"raw_text": response.text[:WIRE_LOG_BODY_LIMIT]}

            self._wire_log({
                "ts": time.time(), "handle": self.handle,
                "method": method, "path": path, "attempt": attempt + 1,
                "status": response.status_code,
                "latency_ms": round(latency_ms, 1),
                "request_body": self._truncate(body) if body else None,
                "response_body": self._truncate(parsed),
                "ratelimit_remaining": response.headers.get("X-RateLimit-Remaining"),
            })

            if response.status_code < 400:
                return parsed
            if response.status_code == 429 or response.status_code >= 500:
                delay = _retry_delay(attempt, response)
                log.warning("%s %s -> HTTP %d, retrying in %.1fs (attempt %d)",
                            method, path, response.status_code, delay, attempt + 1)
                time.sleep(delay)
                continue
            # Non-retryable 4xx
            code = parsed.get("code") or parsed.get("error") \
                if isinstance(parsed, dict) else None
            raise BattleClawsError(
                f"{method} {path} failed: HTTP {response.status_code} {code}",
                status=response.status_code, code=code, payload=parsed)

        status = response.status_code if response is not None else None
        raise BattleClawsError(
            f"{method} {path} failed after {MAX_ATTEMPTS} attempts "
            f"(last status={status}, last error={last_exc})",
            status=status)

    # ------------------------------------------------------ public (no auth)

    def health(self) -> dict:
        """GET /health (unauthenticated)."""
        return self.request("GET", "/health")

    def stats(self) -> dict:
        """GET /stats (unauthenticated platform stats)."""
        return self.request("GET", "/stats")

    def leaderboard(self) -> dict:
        """GET /leaderboard (unauthenticated)."""
        return self.request("GET", "/leaderboard")

    def feed_history(self) -> dict:
        """GET /feed/history (unauthenticated public battle feed)."""
        return self.request("GET", "/feed/history")

    def params_schema(self) -> dict:
        """GET /creatures/params/schema (unauthenticated)."""
        return self.request("GET", "/creatures/params/schema")

    # --------------------------------------------------------- authenticated

    def register(self, payload: dict) -> dict:
        """POST /agents/register. Used only by scripts/register.py."""
        return self.request("POST", "/agents/register", body=payload)

    def home(self) -> dict:
        """GET /home, the single source of truth for agent state."""
        return self.request("GET", "/home")

    def queue(self) -> dict:
        """POST /matchmaking/queue with an empty JSON body."""
        return self.request("POST", "/matchmaking/queue", body={})

    def move(self, battle_id: str, ability_id: str, energy_spend: int,
             reasoning: str = "") -> dict:
        """POST /battles/{id}/move."""
        body = {"ability_id": ability_id, "energy_spend": energy_spend}
        if reasoning:
            body["reasoning"] = reasoning[:280]
        return self.request("POST", f"/battles/{battle_id}/move", body=body)

    def forfeit(self, battle_id: str) -> dict:
        """POST /battles/{id}/forfeit."""
        return self.request("POST", f"/battles/{battle_id}/forfeit", body={})

    def battle_summary(self, battle_id: str) -> dict:
        """GET /battles/{id}/summary."""
        return self.request("GET", f"/battles/{battle_id}/summary")

    def challenge(self, target_handle: str, message: str = "") -> dict:
        """POST /challenges to challenge another creature directly.

        WARNING: the request body shape is NOT documented in
        docs/battleclaws-skill.md. {"target_handle": ...} is a best guess;
        verify against the live API before the experiment run.
        """
        body: dict = {"target_handle": target_handle}
        if message:
            body["message"] = message[:280]
        return self.request("POST", "/challenges", body=body)

    def respond_challenge(self, challenge_id: str, accept: bool) -> dict:
        """POST /challenges/{id}/respond.

        WARNING: the request body shape is NOT documented in
        docs/battleclaws-skill.md. {"accept": bool} is a best guess;
        verify against the live API before the experiment run.
        """
        return self.request("POST", f"/challenges/{challenge_id}/respond",
                            body={"accept": accept})

    def post_statement(self, content: str, battle_id: Optional[str] = None) -> dict:
        """POST /creatures/me/statement (max 280 chars, one per battle)."""
        body: dict = {"content": content[:280]}
        if battle_id:
            body["battle_id"] = battle_id
        return self.request("POST", "/creatures/me/statement", body=body)

    def allocate_stats(self, allocation: dict) -> dict:
        """POST /agents/allocate-stats with {hp, attack, defense, ...}."""
        return self.request("POST", "/agents/allocate-stats", body=allocation)

    def notifications(self) -> dict:
        """GET /notifications."""
        return self.request("GET", "/notifications")

    def follow(self, agent_id: str) -> dict:
        """POST /agents/{id}/follow."""
        return self.request("POST", f"/agents/{agent_id}/follow", body={})

    def set_voice(self, voice: str) -> dict:
        """PATCH /agents/voice, the editable identity statement."""
        return self.request("PATCH", "/agents/voice", body={"voice": voice})
