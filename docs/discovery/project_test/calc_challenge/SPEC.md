# Challenge: a from-scratch arithmetic expression evaluator

Implement a module **`calc.py`** exposing one function:

```python
def evaluate(expression: str) -> float:
    ...
```

`evaluate` parses and computes the value of an arithmetic expression given as a
string, and **always returns a `float`**.

## Hard constraint

Implement the parser and evaluator **yourself** (e.g. a tokenizer + a
recursive-descent / precedence-climbing parser). You may **not** use any of
`eval`, `exec`, `compile`, the `ast` module, `__import__`, or any third-party
package. Standard library only, and no shelling out. (`test_calc.py` scans
`calc.py`'s source and fails if any of those appear.)

## Number literals

- Non-negative integers: `0`, `42`.
- Decimals, including a leading or trailing dot: `3.5`, `.5`, `10.`.
- No scientific notation, no underscores, no hex. A malformed number such as
  `1.2.3` is an error.
- (Negative values arise only from the unary `-` operator below, never from the
  literal itself.)

## Operators and grammar

Binary: `+` `-` `*` `/` `%` `**`. Unary prefix: `+` `-`. Grouping: `( )`.
Whitespace between tokens is insignificant (`" 2 + 3 "` == `"2+3"`).

Precedence, **lowest to highest**, matching Python's own rules exactly:

| level | operators        | associativity |
|-------|------------------|---------------|
| 1     | `+` `-` (binary) | left          |
| 2     | `*` `/` `%`      | left          |
| 3     | `+` `-` (unary)  | (prefix)      |
| 4     | `**`             | right         |

Consequences to get right (these are the whole point of the challenge):

- `**` is **right-associative**: `2**3**2` == `2**(3**2)` == `512.0`.
- A unary minus **outside** a power binds **looser** than the power, so
  `-3**2` == `-(3**2)` == `-9.0`, while `(-3)**2` == `9.0`.
- A unary sign **inside** the exponent is fine: `2**-2` == `0.25`.
- Unary operators may chain: `--3` == `3.0`, `-+-3` == `3.0`, `2+-3` == `-1.0`.
- `/` is **true division** (`7/2` == `3.5`).
- `%` follows Python's sign rule: `-7%3` == `2.0`, `7%-3` == `-2.0`.

## Errors

- Any syntactically malformed expression raises **`ValueError`**. This includes:
  an empty or whitespace-only string, a dangling operator (`"2+"`, `"2**"`), a
  leading binary operator (`"*3"`), two numbers with no operator (`"2 3"`),
  unmatched parentheses (`"(2+3"`, `"2+3)"`), empty parentheses (`"()"`), an
  unknown character (`"2@3"`), a malformed number (`"1.2.3"`), and implicit
  multiplication (`"2(3)"`).
- Division or modulo by zero raises **`ZeroDivisionError`** (`"1/0"`, `"5%0"`,
  `"1/(3-3)"`).

## Done means

`python -m unittest test_calc` passes with zero failures and zero errors.
