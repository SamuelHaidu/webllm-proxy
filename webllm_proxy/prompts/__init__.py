"""Every prompt this tool injects lives in a `.md` file in this package, not in
`.py` string constants -- `loader.default_store` (a `domain.ports.PromptStore`)
reads them. See docs/refactor/PROGRESS.md for why (workaround copy this dense
belongs in prose files, not buried in code)."""
