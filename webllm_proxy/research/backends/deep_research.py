"""ChatGPT Deep Research backend -- NOT YET IMPLEMENTED.

Triggering Deep Research over this proxy's browser transport needs a
reverse-engineering session that hasn't happened yet: capture the *outgoing*
`f/conversation` request body (via the existing CDP `Fetch` rewrite hook,
`providers/chatgpt/__init__.py` `on_fetch_paused`/`_apply_overrides`) while
toggling Deep Research on in the real chatgpt.com UI, to find the trigger
field. Candidates noted during planning (unverified):
`system_hints`, `conversation_mode`, `enabled_tools`/`disabled_tools`. See
docs/discovery/2026-07-10-tool-calling.md and docs/refactor/PROGRESS.md
(Phase C) for the full context.

`available()` always returns False until that trigger is known and this class
is filled in, so `research.backends.resolve_backend` always falls back to
`emulated.EmulatedResearchBackend` -- which is the correct behavior anyway on
the account this was built against (confirmed free-tier; Deep Research is a
paid-tier ChatGPT feature).
"""


class DeepResearchBackend:
    name = "deep_research"

    def available(self, session) -> bool:
        return False

    def run(self, request, *, session, on_progress) -> str:
        raise NotImplementedError(
            "Deep Research trigger not yet discovered -- see this module's docstring "
            "and docs/refactor/PROGRESS.md Phase C"
        )
