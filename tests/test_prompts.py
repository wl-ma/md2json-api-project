from __future__ import annotations

from md2json_api.prompts import COMMON_RULES, AUDIT_RULES


def test_prompts_require_numbered_exercises_to_be_split() -> None:
    assert "split explicitly numbered exercises/problems into separate items" in COMMON_RULES
    assert "merged exercises/problems" in AUDIT_RULES
