from __future__ import annotations

from typing import Iterable

NEGATION_TERMS = (
    "沒有",
    "無",
    "否認",
    "都沒有",
    "都沒",
    "沒這些",
    "不會",
    "沒有以上",
    "無上述",
)

CONTRAST_TERMS = ("但是", "可是", "不過", "但")
CLAUSE_BOUNDARIES = ("。", "！", "？", "；", "，", ",", ";", "\n")

RED_FLAG_KEYWORDS = (
    "突發胸痛",
    "劇烈胸痛",
    "胸痛",
    "胸悶冒冷汗",
    "呼吸困難",
    "喘不過氣",
    "無法呼吸",
    "半邊無力",
    "嘴歪",
    "說話不清",
    "中風",
    "大量出血",
    "血流不止",
    "意識不清",
    "昏迷",
    "叫不醒",
    "劇烈頭痛",
    "視力模糊",
    "抽搐",
)

NEGATED_RED_FLAG_PHRASES = (
    "意識清楚",
    "意識正常",
    "沒有胸痛",
    "無胸痛",
    "沒有呼吸困難",
    "無呼吸困難",
    "沒有大量出血",
    "無大量出血",
    "沒有半邊無力",
    "無半邊無力",
    "沒有劇烈頭痛",
    "無劇烈頭痛",
    "沒有意識不清",
    "無意識不清",
)


def is_negated_keyword(text: str, keyword: str, window: int = 30, start_index: int | None = None) -> bool:
    index = text.find(keyword) if start_index is None else start_index
    if index < 0:
        return False
    if keyword in {"無法呼吸"}:
        return False
    context = text[max(0, index - window):index]
    if any(marker in context for marker in CONTRAST_TERMS):
        return False
    return any(marker in context for marker in NEGATION_TERMS)


def contains_non_negated(text: str, keywords: Iterable[str]) -> bool:
    return any(contains_non_negated_keyword(text, keyword) for keyword in keywords)


def contains_non_negated_keyword(text: str, keyword: str) -> bool:
    """Return true when any occurrence is positive within its local clause.

    This is separate from the established red-flag parser so Department evidence
    can inspect every occurrence without changing existing safety behavior.
    """
    source = str(text or "")
    target = str(keyword or "")
    if not source or not target:
        return False
    start = source.find(target)
    while start >= 0:
        if not _occurrence_is_negated(source, target, start):
            return True
        start = source.find(target, start + 1)
    return False


def grounded_source_supports_positive_normalization(text: str) -> bool:
    """Accept normalized-only evidence only from an unambiguous positive span.

    This gate is used only when the canonical concept is absent from the raw
    source. In that situation Backend cannot associate a mixed-polarity clause
    with the AI-normalized concept, so any negation cue makes the span unsafe.
    Literal concepts continue to use occurrence-level polarity instead.
    """
    source = str(text or "").strip()
    if not source:
        return False
    return not any(marker in source for marker in NEGATION_TERMS)


def _occurrence_is_negated(text: str, keyword: str, start: int) -> bool:
    prefix = text[:start]
    boundary_end = max((prefix.rfind(marker) + len(marker) for marker in CLAUSE_BOUNDARIES), default=0)
    contrast_end = max((prefix.rfind(marker) + len(marker) for marker in CONTRAST_TERMS), default=0)
    scoped_prefix = prefix[max(boundary_end, contrast_end):]
    if any(marker in scoped_prefix for marker in NEGATION_TERMS):
        return True

    suffix = text[start + len(keyword):].lstrip()
    return any(suffix.startswith(marker) for marker in NEGATION_TERMS)


def strip_negated_red_flags(text: str) -> str:
    original = str(text or "")
    remove_spans: list[tuple[int, int]] = []
    for phrase in NEGATED_RED_FLAG_PHRASES:
        start = original.find(phrase)
        while start >= 0:
            remove_spans.append((start, start + len(phrase)))
            start = original.find(phrase, start + len(phrase))
    for keyword in RED_FLAG_KEYWORDS:
        start = original.find(keyword)
        while start >= 0:
            if is_negated_keyword(original, keyword, start_index=start):
                remove_spans.append((start, start + len(keyword)))
            start = original.find(keyword, start + len(keyword))
    if not remove_spans:
        return original

    chars = list(original)
    for start, end in remove_spans:
        for index in range(start, end):
            chars[index] = ""
    return "".join(chars)
