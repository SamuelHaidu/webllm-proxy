import unittest

import is_prime_buggy as m


class TestIsPrime(unittest.TestCase):
    def test_small_primes(self):
        for p in (2, 3, 5, 7, 11, 13, 17):
            self.assertTrue(m.is_prime(p), f"{p} is prime")

    def test_non_primes(self):
        for n in (0, 1, 4, 6, 8, 12, 15):
            self.assertFalse(m.is_prime(n), f"{n} is not prime")

    def test_perfect_squares_of_primes_are_not_prime(self):
        # The tricky edge case: n == i * i exactly, for an odd prime i.
        for n in (9, 25, 49, 121, 169):
            self.assertFalse(m.is_prime(n), f"{n} == p*p is not prime")


class TestPrimesBelow(unittest.TestCase):
    def test_primes_below_30(self):
        self.assertEqual(
            m.primes_below(30),
            [2, 3, 5, 7, 11, 13, 17, 19, 23, 29],
        )


if __name__ == "__main__":
    unittest.main()
