"""Entry point for `python -m webllm_proxy` and the `webllm-proxy` console
script (see `[project.scripts]` in pyproject.toml). Argument parsing and the
subcommands live in `cli.py`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
