"""
Read-only HTTP client for the Frigate API.

This module exposes ONLY GET helpers. Any future write path lives in a separate
module behind an explicit enable_writes config flag (Phase 2 only).
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class FrigateClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # NOTE: this class has no .post / .put / .delete. The omission is intentional.

    def get(self, path: str, params: Optional[dict] = None) -> dict | list:
        url = f"{self.base_url}{path}"
        r = self._session.get(url, params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_events(
        self,
        after: Optional[float] = None,
        before: Optional[float] = None,
        cameras: Optional[list[str]] = None,
        labels: Optional[list[str]] = None,
        limit: int = 200,
        has_snapshot: Optional[bool] = None,
    ) -> list[dict]:
        """
        Paginate-friendly events fetch. Returns a list of event dicts.

        Frigate sorts by start_time desc. To page backwards, use the OLDEST
        start_time in the previous page as the next call's `before`.
        """
        params: dict = {"limit": limit}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        if cameras:
            params["cameras"] = ",".join(cameras)
        if labels:
            params["labels"] = ",".join(labels)
        if has_snapshot is not None:
            params["has_snapshot"] = 1 if has_snapshot else 0
        result = self.get("/api/events", params=params)
        if not isinstance(result, list):
            logger.warning("frigate /api/events returned non-list payload — coercing to []")
            return []
        return result

    def get_stats(self) -> dict:
        result = self.get("/api/stats")
        return result if isinstance(result, dict) else {}

    def get_config(self) -> dict:
        result = self.get("/api/config")
        return result if isinstance(result, dict) else {}
