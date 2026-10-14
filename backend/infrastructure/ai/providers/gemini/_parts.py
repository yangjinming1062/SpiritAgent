from collections.abc import Iterator


def iter_parts(body: dict) -> Iterator[dict]:
    for candidate in body.get("candidates") or []:
        yield from (candidate.get("content") or {}).get("parts") or []
