"""Pure model-namespacing + merge logic for the gateway (no I/O, no Flask), so
it is trivially unit-testable. Every upstream model is presented as
`<provider>__<slug>`; a request carries that id back, which we split to pick an
upstream and restore the real slug."""

from __future__ import annotations

DELIM = "__"


def join_model(provider: str, slug: str) -> str:
    return f"{provider}{DELIM}{slug}"


def split_model(model_id: str | None) -> tuple[str | None, str | None]:
    """`chatgpt__gpt-5` -> ("chatgpt", "gpt-5"); only the FIRST delimiter
    splits, so a slug may itself contain `__`. An id with no delimiter is
    un-namespaced -> (None, model_id); empty -> (None, None)."""
    if not model_id:
        return None, None
    provider, sep, slug = model_id.partition(DELIM)
    if not sep:
        return None, model_id
    return provider, slug


def merge_models(per_upstream: dict[str, list[dict]]) -> dict:
    """Merge each upstream's OpenAI `/v1/models` `data` into one namespaced list
    (sorted by provider for stable output; entries without an `id` are dropped)."""
    data: list[dict] = []
    for provider in sorted(per_upstream):
        for m in per_upstream[provider] or []:
            slug = m.get("id")
            if not slug:
                continue
            item = dict(m)
            item["id"] = join_model(provider, slug)
            item["_provider"] = provider
            data.append(item)
    return {"object": "list", "data": data}


def denamespace_body(body: dict) -> tuple[str | None, dict]:
    """-> (provider, body-with-real-model). `provider` is None when the
    request's `model` is not namespaced (the caller rejects that)."""
    provider, slug = split_model(body.get("model"))
    if provider is None:
        return None, body
    new = dict(body)
    new["model"] = slug
    return provider, new
