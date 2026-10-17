import unittest

from app.server import dispatch


class ServerTest(unittest.TestCase):
    def test_version(self):
        self.assertEqual(dispatch("/version"), (200, {"version": "0.1.0"}))

    def test_unknown_route(self):
        self.assertEqual(dispatch("/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
