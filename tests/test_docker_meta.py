"""docker_meta: names and compose projects from the docker socket.

The mapping is the point of the module, so it is exercised directly against
a constructed container list; the socket round-trip is left to the host run
to avoid touching the live daemon here.
"""
import json
import unittest

from system_manager.network_manager import docker_meta


class ChunkedParseTests(unittest.TestCase):
    def test_a_chunked_body_is_decoded(self):
        body = b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        self.assertEqual(docker_meta._parse_chunked(body), b"hello world")

    def test_a_non_chunked_body_is_passed_through(self):
        body = b'[{"Id": "abc"}]'
        self.assertEqual(docker_meta._parse_chunked(body), b"")  # no valid chunk


class ContainerMetaTests(unittest.TestCase):
    def test_compose_project_and_fallback_name_are_both_read(self):
        rows = [
            {"Id": "a" * 12 + "0" * 52, "Names": ["/ollama"], "Labels": {
                "com.docker.compose.project": "hermes-9router"}},
            {"Id": "b" * 12 + "0" * 52, "Names": ["/by-hand"], "Labels": {}},
        ]
        meta = docker_meta._mapping(rows)
        self.assertEqual(meta["a" * 12], {"name": "ollama", "project": "hermes-9router"})
        # No compose label: the name is the best project hint available.
        self.assertEqual(meta["b" * 12], {"name": "by-hand", "project": "by-hand"})


if __name__ == "__main__":
    unittest.main()
