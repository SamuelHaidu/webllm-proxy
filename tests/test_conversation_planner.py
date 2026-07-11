"""ConversationPlanner unit tests (no browser): continuity detection, preamble
injection, and reasoning-effort normalization."""

from webllm_proxy.application.chat import ConversationPlanner, normalize_effort


def test_first_turn_no_tools_no_system_is_just_the_user_text():
    planner = ConversationPlanner()
    text, new_conv = planner.plan_turn([{"role": "user", "content": "hi"}], None, "auto", None)
    assert text == "hi"
    assert new_conv is True


def test_first_turn_with_system_gets_preamble_and_framing():
    planner = ConversationPlanner()
    messages = [{"role": "system", "content": "Be terse."}, {"role": "user", "content": "hi"}]
    text, new_conv = planner.plan_turn(messages, None, "auto", None)
    assert new_conv is True
    assert "Be terse." in text
    assert "USER REQUEST" in text
    assert text.rstrip().endswith("hi")


def test_growing_history_continues_and_sends_only_new_tail():
    planner = ConversationPlanner()
    first = [{"role": "user", "content": "hi"}]
    planner.plan_turn(first, None, "auto", None)
    second = [
        *first,
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]
    text, new_conv = planner.plan_turn(second, None, "auto", None)
    assert new_conv is False
    assert text == "again"


def test_non_continuing_history_restarts_conversation():
    planner = ConversationPlanner()
    planner.plan_turn([{"role": "user", "content": "hi"}], None, "auto", None)
    # not a pure append of the previous turn -> fresh conversation
    text, new_conv = planner.plan_turn(
        [{"role": "user", "content": "different"}], None, "auto", None
    )
    assert new_conv is True
    assert text == "different"


def test_normalize_effort_maps_openai_levels():
    assert normalize_effort({"reasoning_effort": "high"}) == "max"
    assert normalize_effort({"reasoning": {"effort": "low"}}) == "standard"
    assert normalize_effort({}) is None
    assert normalize_effort({"reasoning_effort": "nonsense"}) is None
