"""Shared helpers for the dashboard UX tests (WYZE-022 G7, G8, G9, G13).

Synthetic data only: the stored database holds the synthetic positive scenario
and its detector findings, PHANTOM_MODE renders the synthetic generator, and
nothing here touches a network, a camera, or an LLM.
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from optimizer import db
from optimizer.analysis import normalize_events, run_all
from optimizer.analysis.service_schedule import CATEGORY_ALIASES
from optimizer.patterns import store, synthetic

from .pattern_helpers import positive_payloads, positive_run

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "optimizer" / "web"
PAGES = ("/", "/recommendations", "/trends", "/cost")

# Words that name, or stand for, a household service: the service learner's
# categories and alias tokens, plus generic service words. Generic object words
# the aliases also use (truck, driver) are left out: they are base labels.
SERVICE_TERMS = frozenset(
    set(CATEGORY_ALIASES)
    | {tok for aliases in CATEGORY_ALIASES.values() for alias in aliases
       for tok in alias.split("_")}
    | {"service", "services", "deliveries", "pickup", "courier", "collection", "postal",
       "parcel", "trash"}
) - {"truck", "driver"}
SERVICE_RE = re.compile(r"\b(?:%s)\b" % "|".join(sorted(SERVICE_TERMS)), re.I)


@lru_cache(maxsize=None)
def _findings_json() -> str:
    """Detector findings for the synthetic scenario, derived as run_once does."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    for e in positive_payloads():
        db.upsert_event(conn, e)
    events = normalize_events(db.events_in_window(conn, 0.0, synthetic.span_now()))
    conn.close()
    return json.dumps(run_all(events, {}, schedule_events=events,
                              timezone_name=synthetic.TZ_NAME))


def findings() -> dict:
    return json.loads(_findings_json())


def build_db(path: Path, findings_override: Optional[dict] = None) -> Path:
    """One ok analysis run and its stored synthetic patterns. Like a production
    rule-layer run (run_once), it records zero tokens and zero cost."""
    db.init_db(path)
    conn = db.connect(path)
    store.apply_schema(conn)
    run = positive_run()
    run_id = db.start_run(conn, 0.0, 1.0)
    db.finish_run(conn, run_id, status="ok", findings=findings_override or findings(),
                  claude_used=False, claude_input_tokens=0, claude_output_tokens=0,
                  claude_cost_usd=0.0)
    store.insert_pattern_run(conn, run_id, "ok", 1.0, 2.0,
                             window_first_date=run.first_date.isoformat(),
                             window_last_date=run.last_date.isoformat(), summary=run.summary)
    store.insert_pattern_results(conn, run_id, list(run.patterns))
    conn.close()
    return path


def client(monkeypatch, tmp_path, db_path: Optional[Path] = None, *, phantom=False, **env):
    monkeypatch.setenv("PHANTOM_MODE", "1" if phantom else "0")
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIMIZER_DB_PATH", str(db_path or tmp_path / "must-not-exist.db"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from optimizer.web.dashboard import create_app
    return create_app().test_client()


# ---- a small HTML tree, enough to check layout -----------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: dict, parent: Optional["Node"]):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list = []   # Node or str, in document order

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(self, tag: str, **attrs) -> list["Node"]:
        return [n for n in self.iter() if n.tag == tag
                and all(n.attrs.get(k) == v for k, v in attrs.items())]

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def text(self) -> str:
        """Visible text, whitespace collapsed (script and style excluded)."""
        if self.tag in ("script", "style"):
            return ""
        parts = [c if isinstance(c, str) else c.text() for c in self.children]
        return re.sub(r"\s+", " ", " ".join(parts)).strip()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(
            Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


def section(root: Node, section_id: str) -> Node:
    found = root.find_all("section", id=section_id)
    assert len(found) == 1, (section_id, len(found))
    return found[0]
