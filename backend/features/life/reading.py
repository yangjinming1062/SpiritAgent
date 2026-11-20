"""阅读原件留在步骤中，事件和按主题回忆只取相应篇幅的原文摘录。"""

from typing import Any

from infrastructure.ai.token_budget import fit_text_prefix, json_tokens
from infrastructure.search import extract_search_terms

from .actions import ReadingResult, ReadingResultView

# 事件摘要会与其他生活事件共同提供给后续判断；明确检索时按调用模型窗口另行取材。
READING_EVENT_TOKENS = 400


def reading_excerpt(
    reading: ReadingResult | ReadingResultView, *, query: str = "", budget: int = READING_EVENT_TOKENS
) -> dict[str, Any]:
    terms = extract_search_terms(query)

    def relevance(text: str) -> int:
        lowered = text.lower()
        return sum(term in lowered for term in terms)

    result: dict[str, Any] = {
        "notes": "",
        "notes_excerpted": True,
        "sources": [],
        "source_count": len(reading.sources),
    }
    room = max(0, budget - json_tokens(result) - 8)
    notes_room = room * 2 // 3
    paragraphs = reading.notes.split("\n\n")
    ranked = sorted(enumerate(paragraphs), key=lambda item: -relevance(item[1]))
    excerpts: list[tuple[int, str]] = []
    for index, paragraph in ranked:
        text = fit_text_prefix(paragraph, notes_room)
        if not text:
            break
        if len(text) < len(paragraph) and relevance(paragraph) > relevance(text):
            lowered = paragraph.lower()
            match = min(lowered.index(term) for term in terms if term in lowered and term not in text.lower())
            text = fit_text_prefix(paragraph[max(0, match - len(text) // 4) :], notes_room)
        excerpts.append((index, text))
        notes_room -= json_tokens(text) + 2
    notes = "\n\n".join(text for _, text in sorted(excerpts))
    result.update(notes=notes, notes_excerpted=notes != reading.notes)
    room -= json_tokens(notes)
    sources = sorted(
        reading.sources,
        key=lambda source: (source.url in notes, relevance(source.title)),
        reverse=True,
    )
    for source in sources:
        item = source.model_dump(mode="json", exclude={"content"})
        cost = json_tokens(item) + 2
        if cost <= room:
            result["sources"].append(item)
            room -= cost
    return result
