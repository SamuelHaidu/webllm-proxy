"""Default prompt store: reads `prompts/system_prompts/<name>.md`, trimmed,
with optional `{placeholder}` substitution."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "prompts" / "system_prompts"


@cache
def _read(directory: Path, name: str) -> str:
    return (directory / f"{name}.md").read_text(encoding="utf-8").strip()


class MarkdownPromptStore:
    def __init__(self, directory: Path = _DIR):
        self._dir = directory

    def get(self, name: str, /, **subs: str) -> str:
        text = _read(self._dir, name)
        return text.format(**subs) if subs else text


default_store = MarkdownPromptStore()
