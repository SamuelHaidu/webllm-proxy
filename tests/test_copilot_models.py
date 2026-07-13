"""copilot model discovery: `parse_manifest` flattens the RefreshNavPane
capability manifest's `availableModelSelectionOptions` (plain items + one level
of itemGroup nesting) into a flat list, verbatim, no filtering/classification.
Fixture is the real captured shape (see docs/discovery/2026-07-13-copilot-live-test.md)."""

from webllm_proxy.providers.copilot.models import parse_manifest

_REAL_META = {
    "defaultModelSelectionId": "Magic",
    "availableModelSelectionOptions": [
        {
            "id": "Magic",
            "type": "item",
            "menuItemTitle": "Auto",
            "menuItemDescription": "Decides how long to think",
            "sectionNumber": 1,
        },
        {
            "id": "Chat",
            "type": "item",
            "menuItemTitle": "Quick Response",
            "menuItemDescription": "Answers right away",
            "sectionNumber": 1,
        },
        {
            "id": "Reasoning",
            "type": "item",
            "menuItemTitle": "Think Deeper",
            "menuItemDescription": "Think longer for better answers",
            "sectionNumber": 1,
        },
        {
            "itemGroup": [
                {
                    "id": "Gpt_5_5_Chat",
                    "type": "item",
                    "menuItemTitle": "GPT 5.5 Quick Response",
                    "shortTitle": "GPT 5.5 Quick",
                    "sectionNumber": 2,
                },
                {
                    "id": "Gpt_5_5_Reasoning",
                    "type": "item",
                    "menuItemTitle": "GPT 5.5 Think Deeper",
                    "shortTitle": "GPT 5.5 Think",
                    "sectionNumber": 2,
                },
            ],
            "id": "OpenAI",
            "type": "itemGroup",
            "menuItemTitle": "GPT",
            "menuItemDescription": "OpenAI",
            "sectionNumber": 2,
        },
    ],
    "totalNumberOfSections": 2,
}


def test_parse_manifest_flattens_real_shape():
    items = parse_manifest({"data": _REAL_META})
    ids = [i["id"] for i in items]
    assert ids == ["Magic", "Chat", "Reasoning", "Gpt_5_5_Chat", "Gpt_5_5_Reasoning"]


def test_parse_manifest_carries_titles_verbatim_no_mapping():
    items = parse_manifest({"data": _REAL_META})
    by_id = {i["id"]: i for i in items}
    assert by_id["Reasoning"]["title"] == "Think Deeper"
    assert by_id["Chat"]["title"] == "Quick Response"
    # itemGroup leaves prefer menuItemTitle over shortTitle when both are present
    assert by_id["Gpt_5_5_Chat"]["title"] == "GPT 5.5 Quick Response"
    # no invented flags (e.g. no family/effort classification) beyond id/title/description
    assert set(by_id["Reasoning"]) == {"id", "title", "description"}


def test_parse_manifest_missing_data_is_empty():
    assert parse_manifest({"data": None}) == []
    assert parse_manifest({}) == []
    assert parse_manifest({"data": {"availableModelSelectionOptions": "not-a-list"}}) == []


def test_parse_manifest_skips_malformed_entries():
    meta = {
        "availableModelSelectionOptions": [
            None,
            "not-a-dict",
            {"type": "item"},  # no id -> dropped
            {"type": "item", "id": "OK", "menuItemTitle": "Ok Title"},
            {"type": "itemGroup", "itemGroup": None},  # empty group -> nothing added
        ]
    }
    items = parse_manifest({"data": meta})
    assert items == [{"id": "OK", "title": "Ok Title", "description": None}]


def test_parse_manifest_title_falls_back_to_id():
    meta = {"availableModelSelectionOptions": [{"type": "item", "id": "Bare"}]}
    items = parse_manifest({"data": meta})
    assert items == [{"id": "Bare", "title": "Bare", "description": None}]
