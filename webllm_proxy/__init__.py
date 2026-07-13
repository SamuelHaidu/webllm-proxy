"""webllm-proxy: browser-backed local API bridges over login-only web LLMs.

A single OpenAI-compatible server that fronts different web LLM backends
(ChatGPT web, Databricks Genie/llmproxy, Microsoft Copilot), driven through a
stealth browser. Each backend is a 2-method `Provider` (see `providers/base.py`);
the reusable browser transport lives in `gateways/cloakbrowser/`.
"""

from ._version import __version__

__all__ = ["__version__"]
