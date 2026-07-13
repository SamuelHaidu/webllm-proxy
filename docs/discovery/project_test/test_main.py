import unittest

import main


class TestFib(unittest.TestCase):
    def test_base_cases(self):
        self.assertEqual(main.fib(0), 0)
        self.assertEqual(main.fib(1), 1)

    def test_larger_value(self):
        self.assertEqual(main.fib(10), 55)

    def test_negative_input(self):
        with self.assertRaises(ValueError):
            main.fib(-1)


class TestPrimeFunctions(unittest.TestCase):
    def test_is_prime_edge_cases(self):
        for value in (-10, 0, 1):
            self.assertFalse(main.is_prime(value))

    def test_is_prime_values(self):
        self.assertTrue(main.is_prime(2))
        self.assertTrue(main.is_prime(13))
        self.assertFalse(main.is_prime(9))
        self.assertFalse(main.is_prime(100))

    def test_primes_up_to(self):
        self.assertEqual(main.primes_up_to(1), [])
        self.assertEqual(main.primes_up_to(10), [2, 3, 5, 7])


class TestQuicksort(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(main.quicksort([]), [])
        self.assertEqual(main.quicksort([1]), [1])

    def test_duplicates_and_order(self):
        values = [3, 1, 2, 1]
        self.assertEqual(main.quicksort(values), [1, 1, 2, 3])
        self.assertEqual(values, [3, 1, 2, 1])

    def test_reverse_sorted(self):
        self.assertEqual(main.quicksort([5, 4, 3, 2, 1]), [1, 2, 3, 4, 5])


class TestBenchmark(unittest.TestCase):
    def test_invalid_repeat(self):
        with self.assertRaises(ValueError):
            main.benchmark(lambda: None, repeat=0)

    def test_returns_result_and_time(self):
        result, seconds = main.benchmark(lambda: 42, repeat=2)
        self.assertEqual(result, 42)
        self.assertGreaterEqual(seconds, 0)


if __name__ == "__main__":
    unittest.main()
