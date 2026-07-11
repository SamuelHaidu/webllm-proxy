"""webllm-proxy: browser-backed local API bridges over login-only web LLMs.

A single tool that fronts different web LLM backends (ChatGPT web, Databricks
Genie/llmproxy, ...) with a standard API, driven through a stealth browser.
Each backend is a `Provider` (see `providers/base.py`); the reusable browser
transport + CDP capture live in `core/`.
"""
from ._version import __version__

__all__ = ["__version__"]
