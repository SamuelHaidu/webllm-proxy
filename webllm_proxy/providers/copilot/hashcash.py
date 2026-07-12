"""Hashcash-style proof-of-work solver for the consumer `/c/api/chat` anti-bot
challenge (`{"event":"challenge","method":"hashcash","parameter":"<res>:<bits>"}`).

The exact digest-input ordering and difficulty encoding are inferred from a
capture where `parameter` ended in `:1` and the accepted `token` was `"0"`
(i.e. difficulty 1, trivial). We treat difficulty as a **leading-zero-bit**
count over `sha256(parameter + token)`. If a future capture shows a different
ordering (e.g. `token + parameter`) or hex-char counting, adjust here only —
nothing else in the package depends on it. See
`docs/protocol/copilot-protocol.md` §2.4 / open item #2.
"""

from __future__ import annotations

import hashlib

from .exceptions import ChallengeError


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        # count leading zeros in this byte, then stop
        bits += 8 - byte.bit_length()
        break
    return bits


def parse_parameter(parameter: str) -> tuple[str, int]:
    """`"<resource>:<difficulty>"` -> (resource-including-nothing, difficulty)."""
    _, _, diff = parameter.rpartition(":")
    try:
        difficulty = int(diff)
    except ValueError:
        difficulty = 1
    return parameter, difficulty


def solve(parameter: str, *, difficulty: int | None = None, max_iter: int = 1 << 24) -> str:
    """Return a `token` (ASCII decimal nonce) whose `sha256(parameter+token)` has
    at least `difficulty` leading zero bits. Raises `ChallengeError` if it can't
    be found within `max_iter` tries."""
    _, diff = parse_parameter(parameter)
    if difficulty is None:
        difficulty = diff
    if difficulty <= 0:
        return "0"
    prefix = parameter.encode()
    for n in range(max_iter):
        token = str(n)
        if _leading_zero_bits(hashlib.sha256(prefix + token.encode()).digest()) >= difficulty:
            return token
    raise ChallengeError(f"hashcash: no solution for difficulty {difficulty} within {max_iter}")
