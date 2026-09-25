"""
Opaque recurring-visit patterns.

Collapses Frigate tracks into visits, classifies each visit by behavior, and
finds weekly time-locked windows that beat their own local base rate under an
exact binomial test with Benjamini-Hochberg false-discovery control.

Patterns report WHEN and HOW activity recurs. They never report WHAT it is:
identity comes only from explicit upstream labels through a configured map
that ships empty. Nothing in this package reads camera media or calls an LLM.
"""
