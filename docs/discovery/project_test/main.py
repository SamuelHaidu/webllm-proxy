"""Small benchmark playground: a few pure algorithms plus a tiny timing
harness. Deliberately mid-complexity, used as a target for exercising the
coding agent (has clear, deterministic behavior plus some edge cases to test).
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from typing import Any


def fib(n: int) -> int:
    """Return the n-th Fibonacci number (0-indexed: fib(0)=0, fib(1)=1)."""
    if n < 0:
        raise ValueError("n must be non-negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def is_prime(n: int) -> bool:
    """True if n is a prime number. Numbers < 2 are not prime."""
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


def primes_up_to(limit: int) -> list[int]:
    """All primes <= limit, via a sieve of Eratosthenes."""
    if limit < 2:
        return []
    sieve = bytearray([1]) * (limit + 1)
    sieve[0] = sieve[1] = 0
    for i in range(2, int(limit**0.5) + 1):
        if sieve[i]:
            sieve[i * i :: i] = bytearray(len(sieve[i * i :: i]))
    return [i for i in range(limit + 1) if sieve[i]]


def quicksort(seq: Sequence[Any]) -> list[Any]:
    """Return a new sorted list from seq (not in place)."""
    items = list(seq)
    if len(items) <= 1:
        return items
    pivot = items[len(items) // 2]
    less = [x for x in items if x < pivot]
    equal = [x for x in items if x == pivot]
    greater = [x for x in items if x > pivot]
    return quicksort(less) + equal + quicksort(greater)


def benchmark(func: Callable[..., Any], *args: Any, repeat: int = 5) -> tuple[Any, float]:
    """Call func(*args) `repeat` times; return (last_result, best_seconds)."""
    if repeat < 1:
        raise ValueError("repeat must be >= 1")
    best = float("inf")
    result: Any = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = func(*args)
        best = min(best, time.perf_counter() - start)
    return result, best


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface parser."""
    parser = argparse.ArgumentParser(description="Run algorithm playground tasks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fib_parser = subparsers.add_parser("fib", help="Calculate a Fibonacci number.")
    fib_parser.add_argument("n", type=int)

    prime_parser = subparsers.add_parser("prime", help="Check if a number is prime.")
    prime_parser.add_argument("n", type=int)

    primes_parser = subparsers.add_parser("primes-up-to", help="List all primes up to a limit.")
    primes_parser.add_argument("limit", type=int)

    sort_parser = subparsers.add_parser("sort", help="Sort integer values.")
    sort_parser.add_argument("values", type=int, nargs="+")

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    match args.command:
        case "fib":
            print(fib(args.n))
        case "prime":
            print(is_prime(args.n))
        case "primes-up-to":
            print(primes_up_to(args.limit))
        case "sort":
            print(quicksort(args.values))


if __name__ == "__main__":
    main()
