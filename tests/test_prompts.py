"""Prompt-loader unit tests (no browser): every prompt name resolves to
non-empty text, loads are cached, and `{placeholder}` substitution works.
Doubles as the "parity" guard for the prompts-to-markdown move (Phase A1) --
each name below existed as a `.py` string constant before that move."""

from webllm_proxy.prompts.loader import MarkdownPromptStore, default_store

_EXPECTED_NAMES_AND_MARKERS = {
    "tool_contract": "<tool>",
    "system_header": "SYSTEM INSTRUCTIONS",
    "user_request_framing": "USER REQUEST",
    "genie_framing": "Genie",
    "style_rules": "concise",
}


def test_every_known_prompt_loads_nonempty():
    for name, marker in _EXPECTED_NAMES_AND_MARKERS.items():
        text = default_store.get(name)
        assert text and marker in text


def test_get_is_cached_across_calls():
    assert default_store.get("tool_contract") is default_store.get("tool_contract")


def test_substitution_with_placeholder(tmp_path):
    (tmp_path / "greeting.md").write_text("Hello, {name}!")
    store = MarkdownPromptStore(directory=tmp_path)
    assert store.get("greeting", name="World") == "Hello, World!"


def test_no_substitution_is_a_plain_read():
    store = MarkdownPromptStore()
    assert store.get("style_rules") == default_store.get("style_rules")
