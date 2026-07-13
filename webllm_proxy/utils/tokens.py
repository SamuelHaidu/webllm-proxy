"""Token counting placeholder. Returns zeros for now (deferred per the rebuild
plan); a later change ports coder/ai-tokenizer for real cross-model counting."""


def usage(prompt: int = 0, completion: int = 0) -> dict:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
