"""E4 static check on committed text.

The S-9a count is a fixed-seed synthetic characterization. No committed
sentence may pair that count with real-world language, so it can never read
as a measured rate for a real camera.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The count, in the forms prose uses for it: "1 of 20", "1/20", "1-of-20",
# "one in twenty", and so on; or 5% when the sentence is about cadence.
FIGURE = re.compile(
    r"\b(?:1|one)(?:\s*/\s*|\s+of\s+|\s+in\s+|-of-|-in-|\s+out\s+of\s+)(?:20|twenty)\b", re.I)
PERCENT = re.compile(r"\b5\s*%|\bfive\s+percent\b|\b5\s+percent\b", re.I)
CADENCE_WORDS = re.compile(r"weekly|biweekly|cadence|S-9a", re.I)
REAL_WORLD = re.compile(
    r"\breal\b|real-world|\bproduction\b|in practice|\baccuracy\b|error\s+rate", re.I)


def _scanned_files() -> list[Path]:
    """Committed text: docs, the env example, source, templates, tests, scripts.
    Listed explicitly, so untracked runtime files are never opened."""
    files = sorted(ROOT.glob("*.md")) + [ROOT / ".env.example"]
    files += sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "src").rglob("*.html"))
    files += sorted((ROOT / "tests").rglob("*.py")) + sorted((ROOT / "scripts").glob("*.sh"))
    return [f for f in files if f.is_file()]


def sentences(text: str) -> list[str]:
    out = []
    for paragraph in re.split(r"\n\s*\n", text):
        joined = " ".join(line.strip(" #*>-") for line in paragraph.splitlines())
        out.extend(s for s in re.split(r"(?<=[.!?;])\s+", joined) if s)
    return out


def offending(text: str) -> list[str]:
    hits = []
    for s in sentences(text):
        figure = FIGURE.search(s) or (PERCENT.search(s) and CADENCE_WORDS.search(s))
        if figure and REAL_WORLD.search(s):
            hits.append(s)
    return hits


def test_e4_positive_and_negative_controls():
    # Built at runtime, so this file never pairs the count with those words itself.
    count, pct = "1 " + "of 20", "5" + "%"
    assert offending(f"Weekly was read in {count} seeds in production.")
    assert offending(f"The weekly error rate is {count.replace(' of ', '/')}.")
    assert offending(f"In practice about {pct} of biweekly patterns read weekly.")
    assert not offending(f"S-9a read weekly in {count} fixed synthetic seeds.")
    assert not offending(f"Weekly in {count} seeds. A real camera was never measured.")


def test_e4_no_committed_string_pairs_the_synthetic_count_with_real_world_language():
    files = _scanned_files()
    assert any(f.name == "ARCHITECTURE.md" for f in files) and len(files) > 40
    assert not any(f.name == "uv.lock" for f in files)
    hits = {str(f.relative_to(ROOT)): offending(f.read_text(errors="replace")) for f in files}
    assert {k: v for k, v in hits.items() if v} == {}
