import unittest
from pathlib import Path

import calc


def ev(expr: str) -> float:
    return calc.evaluate(expr)


class TestValues(unittest.TestCase):
    def test_precedence_and_grouping(self):
        cases = {
            "1+2*3": 7.0,
            "2+3*4": 14.0,
            "2*3+4": 10.0,
            "2*(3+4)": 14.0,
            "(1+2)*(3+4)": 21.0,
            "1+2*3-4/2": 5.0,
        }
        for expr, want in cases.items():
            with self.subTest(expr=expr):
                self.assertEqual(ev(expr), want)

    def test_left_associativity(self):
        self.assertEqual(ev("10-3-2"), 5.0)
        self.assertEqual(ev("8/4/2"), 1.0)
        self.assertEqual(ev("2*3%4"), 2.0)
        self.assertEqual(ev("10%4%3"), 2.0)

    def test_power_is_right_associative(self):
        self.assertEqual(ev("2**3**2"), 512.0)
        self.assertEqual(ev("2**3*2"), 16.0)
        self.assertEqual(ev("2*3**2"), 18.0)
        self.assertEqual(ev("4**0.5"), 2.0)

    def test_unary_and_power_interaction(self):
        self.assertEqual(ev("-3"), -3.0)
        self.assertEqual(ev("+5"), 5.0)
        self.assertEqual(ev("--3"), 3.0)
        self.assertEqual(ev("-+-3"), 3.0)
        self.assertEqual(ev("2+-3"), -1.0)
        self.assertEqual(ev("2*-3"), -6.0)
        self.assertEqual(ev("-3**2"), -9.0)
        self.assertEqual(ev("(-3)**2"), 9.0)
        self.assertEqual(ev("-2**2"), -4.0)
        self.assertEqual(ev("2**-2"), 0.25)

    def test_division_and_modulo(self):
        self.assertEqual(ev("7/2"), 3.5)
        self.assertEqual(ev("7%3"), 1.0)
        self.assertEqual(ev("-7%3"), 2.0)
        self.assertEqual(ev("7%-3"), -2.0)

    def test_decimals_and_whitespace(self):
        self.assertEqual(ev(".5+.5"), 1.0)
        self.assertEqual(ev("1.5*2"), 3.0)
        self.assertEqual(ev("10.0/4"), 2.5)
        self.assertEqual(ev("  2 +  3 * 4 "), 14.0)

    def test_always_returns_float(self):
        result = ev("2+3")
        self.assertIsInstance(result, float)
        self.assertEqual(result, 5.0)


class TestErrors(unittest.TestCase):
    def test_malformed_raises_value_error(self):
        bad = [
            "",
            "   ",
            "()",
            "2+",
            "2*",
            "2**",
            "+",
            "*3",
            "/3",
            "2 3",
            "(2+3",
            "2+3)",
            "2@3",
            "1.2.3",
            "2(3)",
        ]
        for expr in bad:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    ev(expr)

    def test_division_by_zero(self):
        for expr in ["1/0", "5%0", "1/(3-3)"]:
            with self.subTest(expr=expr):
                with self.assertRaises(ZeroDivisionError):
                    ev(expr)


class TestImplementationConstraint(unittest.TestCase):
    # No shortcuts: the whole point is to write a real parser.
    FORBIDDEN = ("eval(", "exec(", "compile(", "__import__")

    def test_no_interpreter_shortcuts_in_source(self):
        source = Path(__file__).with_name("calc.py").read_text(encoding="utf-8")
        for token in self.FORBIDDEN:
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
