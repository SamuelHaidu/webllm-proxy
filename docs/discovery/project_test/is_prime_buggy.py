"""A deliberately buggy prime helper, planted to exercise the emulated thinking
layer of the webllm-agent (see docs/discovery/2026-07-12-emulated-thinking.md).

There is exactly ONE root-cause bug, and it only surfaces on an edge case, so a
model has to actually reason about boundary conditions to find it rather than
eyeballing the code. `test_is_prime_buggy.py` fails until it's fixed.
"""

from __future__ import annotations


def is_prime(n: int) -> bool:
    """True if n is a prime number. Numbers < 2 are not prime."""
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i < n:
        if n % i == 0:
            return False
        i += 2
    return True


def primes_below(limit: int) -> list[int]:
    """All primes strictly below `limit`, checked one by one with is_prime."""
    return [n for n in range(2, limit) if is_prime(n)]
