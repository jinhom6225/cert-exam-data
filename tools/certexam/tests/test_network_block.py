import socket
import unittest
import urllib.request

from tools.certexam.tests import NetworkBlocked


class NetworkBlockTest(unittest.TestCase):
    def test_socket_creation_is_blocked(self):
        with self.assertRaises(NetworkBlocked):
            socket.socket()

    def test_urllib_cannot_reach_network(self):
        with self.assertRaises(Exception):
            urllib.request.urlopen("http://openapi.q-net.or.kr/", timeout=1)


if __name__ == "__main__":
    unittest.main()
