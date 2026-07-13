"""Live model discovery from the Copilot capability manifest.

`POST /chat {"action": "RefreshNavPane"}` is the shell's own data endpoint (not
chat completion); it returns, among other things,
`store.bizchatAsAgentGpt.clientPreferences.modelSelectorMetadata`, an object
shaped:

    {
      "defaultModelSelectionId": "Magic",
      "availableModelSelectionOptions": [
        {"id": "Magic", "type": "item", "menuItemTitle": "Auto", ...},
        {"id": "Chat", "type": "item", "menuItemTitle": "Quick Response", ...},
        {"itemGroup": [{"id": "Gpt_5_5_Chat", "type": "item", ...}, ...],
         "id": "OpenAI", "type": "itemGroup", "menuItemTitle": "GPT", ...},
        ...
      ]
    }

`availableModelSelectionOptions` is a flat/nested mix: plain `item`s at the top
level, and `itemGroup`s that nest a further list of `item`s (one level deep in
every capture seen so far; `_flatten` recurses regardless). No entitlement gate
observed here (unlike Databricks) -- whatever the manifest lists is what the
account can select.
"""

from __future__ import annotations

# In-page: POST the manifest request and return just the one object we need
# (not the ~8 KB manifest) so parsing/validation stays in Python and testable.
MANIFEST_JS = r"""
async () => {
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      credentials: 'include',
      headers: {'content-type': 'application/json', 'accept': 'application/json'},
      body: JSON.stringify({action: 'RefreshNavPane'}),
    });
    if (!res.ok) return {error: res.status};
    const j = await res.json();
    const meta = ((((j || {}).store || {}).bizchatAsAgentGpt || {}).clientPreferences || {})
      .modelSelectorMetadata;
    return {data: meta || null};
  } catch (e) {
    return {error: String(e)};
  }
}
"""


def _flatten(options, out: list[dict]) -> None:
    for it in options or []:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "itemGroup":
            _flatten(it.get("itemGroup"), out)
        elif it.get("type") == "item" and it.get("id"):
            out.append(
                {
                    "id": it["id"],
                    "title": it.get("menuItemTitle") or it.get("shortTitle") or it["id"],
                    "description": it.get("menuItemDescription"),
                }
            )


def parse_manifest(response) -> list[dict]:
    """`{"data": <modelSelectorMetadata>}` -> flattened `[{id, title, description}]`,
    every leaf `item` from `availableModelSelectionOptions` (itemGroups expanded),
    in manifest order, verbatim -- no filtering, no family classification."""
    try:
        options = response["data"]["availableModelSelectionOptions"]
    except (KeyError, TypeError):
        return []
    if not isinstance(options, list):
        return []
    out: list[dict] = []
    _flatten(options, out)
    return out
