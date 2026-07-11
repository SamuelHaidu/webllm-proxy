"""Default `PromptStore` (see `domain.ports`): reads `prompts/<name>.md` next to
this file, trimmed, with optional `{placeholder}` substitution."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def _read(directory: Path, name: str) -> str:
    return (directory / f"{name}.md").read_text(encoding="utf-8").strip()


class MarkdownPromptStore:
    """Reads prompts from `.md` files in a directory (this package's by
    default). Construct one with a different `directory` for tests or an
    alternate prompt source; anything with a matching `get` satisfies the
    `PromptStore` port."""

    def __init__(self, directory: Path = _DIR):
        self._dir = directory

    def get(self, name: str, /, **subs: str) -> str:
        # `name` is positional-only so it can never collide with a `{name}`
        # placeholder passed through **subs (see tests/test_prompts.py).
        text = _read(self._dir, name)
        return text.format(**subs) if subs else text


default_store = MarkdownPromptStore()
