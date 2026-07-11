"""Registry: prefer ChatGPT Deep Research when the account has it; fall back
to the emulated (plain chat + native web search) backend otherwise -- see
`deep_research.py` and `emulated.py` for why the emulated path is the one
guaranteed to work today and ships first."""

from .deep_research import DeepResearchBackend
from .emulated import EmulatedResearchBackend


def resolve_backend(session):
    deep = DeepResearchBackend()
    if deep.available(session):
        return deep
    return EmulatedResearchBackend()
