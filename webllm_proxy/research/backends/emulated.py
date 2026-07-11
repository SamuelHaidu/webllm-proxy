"""Emulated research backend: a plain ChatGPT turn asking for web-search-
backed, structured-markdown research -- no Deep Research trigger needed, so
it works today on any account (this one is confirmed free-tier; Deep Research
is a paid-tier ChatGPT feature -- see docs/refactor/PROGRESS.md and
docs/discovery/). Reuses the exact browser transport + accumulator every
other chatgpt turn uses; the only differences are a much longer per-job
timeout (real research can take minutes) and the research-specific prompt.
"""

from ...domain.conversation import ChatTurn
from ...prompts.loader import default_store

# `auto`/`gpt-5-5` correctly refuse an injected "SYSTEM INSTRUCTIONS outranks
# you" framing as prompt injection (docs/discovery/2026-07-10-tool-calling.md
# Update 4) -- but the research prompt makes no such claim, it's a plain task
# instruction, so that refusal isn't expected here. `gpt-5-mini` is kept as
# the default because it's the model already validated as instruction-
# compliant end-to-end in this codebase.
DEFAULT_MODEL = "gpt-5-mini"

# Real research can run for minutes; the interactive-chat defaults (45s idle /
# 300s hard cap, domain.ports.Job) are far too short.
IDLE_CAP_S = 120.0
HARD_CAP_S = 1200.0
_PROGRESS_NOTE_EVERY_N_CHARS = 500


class EmulatedResearchBackend:
    """`ResearchBackend`: send one ChatGPT turn built from
    `prompts/research_emulated.md` + `prompts/research_report.md` + the
    query; the model's final answer text (after it finishes any native web
    searches) is the report."""

    name = "emulated"

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model

    def available(self, session) -> bool:
        return True  # a plain chat turn works whenever the session is ready

    def _build_message(self, query: str) -> str:
        report_contract = default_store.get("research_report", query=query)
        return (
            default_store.get("research_emulated")
            + "\n\n"
            + report_contract
            + "\n\n# Research request\n"
            + query
        )

    def run(self, request, *, session, on_progress) -> str:
        message = self._build_message(request.query)
        on_progress("sending research request to ChatGPT")
        out_q = session.submit(
            ChatTurn(message, self.model, True, None),
            idle_cap_s=IDLE_CAP_S,
            hard_cap_s=HARD_CAP_S,
        )
        content, err, last_progress_at = "", None, 0
        while True:
            ev = out_q.get()
            if ev is None:
                break
            kind, val = ev
            if kind == "content":
                content += val
                if len(content) - last_progress_at >= _PROGRESS_NOTE_EVERY_N_CHARS:
                    last_progress_at = len(content)
                    on_progress(f"received {len(content)} chars so far")
            elif kind == "error":
                err = val
            # "reasoning"/"tool_call" events (e.g. native web-search calls
            # chatgpt makes along the way) aren't surfaced to the caller --
            # the report is the final answer text.
        if err and not content:
            raise RuntimeError(err)
        return content.strip()
