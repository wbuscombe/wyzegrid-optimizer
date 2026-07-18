"""
Optional ntfy failure alert. Thin HTTP POST to an ntfy topic.

If NTFY_URL / NTFY_TOPIC / NTFY_USER / NTFY_PASS are not all set (the default),
every call is a no-op — the optimizer runs identically whether or not ntfy is
wired up, exactly like the ZMA webhook. The ecosystem ntfy server
(http://192.168.50.7) is `auth-default-access: deny-all`, so publishing requires
authentication; this poster uses HTTP Basic auth. Never raises — a failure is a
logged warning.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def post_alert(
    url: str,
    topic: str,
    user: str,
    password: str,
    message: str,
    *,
    title: str = "wyzegrid-optimizer",
    priority: str = "high",
    tags: str = "warning",
    timeout: float = 3.0,
) -> bool:
    """POST ``message`` to ``{url}/{topic}`` with HTTP Basic auth.

    Returns True on a 2xx response, False otherwise — including when any required
    field (url, topic, user, password) is unset, which is the no-op default.
    Never raises.
    """
    if not (url and topic and user and password):
        return False
    try:
        r = requests.post(
            f"{url.rstrip('/')}/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            auth=(user, password),
            timeout=timeout,
        )
        if r.status_code >= 300:
            logger.warning("ntfy publish returned HTTP %d", r.status_code)
            return False
        return True
    except Exception as e:
        logger.warning("ntfy publish failed: %s", e)
        return False
