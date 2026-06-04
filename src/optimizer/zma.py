"""
Optional ZMA Bot status webhook. Thin HTTP POST.

If ZMA_WEBHOOK_URL is unset (default), every call is a no-op. The optimizer
must run identically whether or not ZMA is wired up — the integration is
strictly additive.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def post_status(
    webhook_url: str,
    event: str,
    payload: Optional[dict] = None,
    timeout: float = 3.0,
) -> bool:
    """
    POST a status event to the ZMA webhook. Returns True if the POST succeeded
    (2xx), False otherwise. Never raises — failure is a logged warning.
    """
    if not webhook_url:
        return False
    body = {
        "source": "wyzegrid-optimizer",
        "event": event,
        "payload": payload or {},
    }
    try:
        r = requests.post(webhook_url, json=body, timeout=timeout)
        if r.status_code >= 300:
            logger.warning("ZMA webhook returned HTTP %d for event=%s", r.status_code, event)
            return False
        return True
    except Exception as e:
        logger.warning("ZMA webhook failed for event=%s: %s", event, e)
        return False
