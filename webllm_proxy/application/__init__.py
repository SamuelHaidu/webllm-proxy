"""Use-case orchestration: the logic that decides WHAT to send upstream and
how to interpret the client's request, independent of Flask (`http/`) and the
browser transport. Depends only on `domain`, `strategies`, `prompts`, `wire`."""
