import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_lowercase_and_internal_space(self):
        self.assertEqual(slugify("Hello World"), "hello-world")


if __name__ == "__main__":
    unittest.main()
