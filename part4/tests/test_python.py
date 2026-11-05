import tempfile
import unittest
from pathlib import Path

from part4.config import (
    config_load_client,
    config_load_peer_server,
    config_load_tracker,
)
from part4.protocol import (
    protocol_parse_request,
    protocol_format_request,
    protocol_parse_reply,
    protocol_format_reply,
)

ROOT = Path(__file__).resolve().parents[1]
MD5 = "d41d8cd98f00b204e9800998ecf8427e"


class ConfigurationTests(unittest.TestCase):
    def test_supplied_configurations(self):
        tracker = config_load_tracker(ROOT / "config/tracker/sconfig")
        self.assertEqual(tracker.listen_port, 3490)
        self.assertEqual(tracker.tracker_directory, "torrents")

        for number in (1, 2, 3):
            with self.subTest(peer=number):
                folder = ROOT / f"config/peer{number}"
                client = config_load_client(folder / "clientThreadConfig.cfg")
                server = config_load_peer_server(folder / "serverThreadConfig.cfg")
                self.assertEqual(client.tracker_port, 3490)
                self.assertEqual(client.tracker_ip, "127.0.0.1")
                self.assertEqual(client.refresh_seconds, 900)
                self.assertEqual(server.listen_port, 5000 + number)
                self.assertEqual(server.shared_directory, "shared")

    def test_crlf_and_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.cfg"
            path.write_bytes(b" 3490 \r\n127.0.0.1\r\n900\r\n")
            self.assertEqual(config_load_client(path).refresh_seconds, 900)

    def test_invalid_client_settings(self):
        cases = [
            "0\n127.0.0.1\n900",
            "65536\n127.0.0.1\n900",
            "-1\n127.0.0.1\n900",
            "abc\n127.0.0.1\n900",
            "3490\n999.0.0.1\n900",
            "3490\n127.0.0.1\n0",
            "3490\n127.0.0.1\n-1",
            "3490\n127.0.0.1",
            "3490\n\n900",
            "3490\n127.0.0.1\n900\nextra",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.cfg"
            for text in cases:
                with self.subTest(settings=text):
                    path.write_text(text)
                    with self.assertRaises(ValueError):
                        config_load_client(path)

    def test_missing_file_and_invalid_server_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.cfg"
            with self.assertRaises(ValueError):
                config_load_client(path)

            for loader in (config_load_tracker, config_load_peer_server):
                for text in ("0\nshared", "70000\nshared", "5001\n", "5001\nshared\nextra"):
                    with self.subTest(loader=loader.__name__, settings=text):
                        path.write_text(text)
                        with self.assertRaises(ValueError):
                            loader(path)


class ProtocolTests(unittest.TestCase):
    def test_request_formats_and_fields(self):
        cases = [
            ({"type": "list"}, "<REQ LIST>\n"),
            ({"type": "get", "filename": "movie.track"}, "<GET movie.track>\n"),
            (
                {
                    "type": "create", "filename": "movie", "filesize": 4096,
                    "description": "Sample_movie", "md5": MD5,
                    "ip": "127.0.0.1", "port": 5001,
                },
                f"<createtracker movie 4096 Sample_movie {MD5} 127.0.0.1 5001>\n",
            ),
            (
                {
                    "type": "update", "filename": "movie",
                    "start_byte": 0, "end_byte": 1024,
                    "ip": "127.0.0.1", "port": 5002,
                },
                "<updatetracker movie 0 1024 127.0.0.1 5002>\n",
            ),
        ]
        for expected, wire in cases:
            with self.subTest(wire=wire):
                self.assertEqual(protocol_format_request(expected), wire)
                self.assertEqual(protocol_parse_request(wire), expected)

    def test_reply_formats_and_fields(self):
        cases = [
            ({"type": "list_begin", "number": 0}, "<REP LIST 0>\n"),
            ({"type": "list_end"}, "<REP LIST END>\n"),
            ({"type": "get_begin"}, "<REP GET BEGIN>\n"),
            ({"type": "get_end", "md5": MD5}, f"<REP GET END {MD5}>\n"),
            (
                {"type": "list_item", "number": 1, "filename": "movie",
                 "filesize": 4096, "md5": MD5},
                f"<1 movie 4096 {MD5}>\n",
            ),
        ]
        for status in ("succ", "fail", "ferr"):
            cases.extend([
                ({"type": "create", "status": status},
                 f"<createtracker {status}>\n"),
                ({"type": "update", "filename": "movie", "status": status},
                 f"<updatetracker movie {status}>\n"),
            ])
        for expected, wire in cases:
            with self.subTest(wire=wire):
                self.assertEqual(protocol_format_reply(expected), wire)
                self.assertEqual(protocol_parse_reply(wire), expected)

    def test_manual_commands_crlf_and_limits(self):
        self.assertEqual(protocol_parse_request("REQ LIST"), {"type": "list"})
        self.assertEqual(protocol_parse_request("<REQ LIST>\r\n"), {"type": "list"})
        self.assertEqual(
            protocol_parse_reply("<REP LIST END>\r\n"), {"type": "list_end"}
        )
        request = protocol_parse_request(
            "<updatetracker f 0 18446744073709551615 127.0.0.1 65535>\n"
        )
        self.assertEqual(request["end_byte"], (1 << 64) - 1)
        self.assertEqual(request["port"], 65535)

    def test_invalid_requests(self):
        cases = [
            "", "<REQ LIST", "<REQ LIST>>", "<REQ LIST extra>",
            "<GET>", "<GET movie>", "<GET ../movie.track>", "<GET .track>",
            "<unknown command>", "<REQ LIST>\n<REQ LIST>\n",
            f"<createtracker f -1 desc {MD5} 127.0.0.1 5001>",
            "<createtracker f 1 desc badmd5 127.0.0.1 5001>",
            "<updatetracker f 10 5 127.0.0.1 5001>",
            "<updatetracker f 0 10 999.0.0.1 5001>",
            "<updatetracker f 0 10 127.0.0.1 0>",
            "<updatetracker f 0 10 127.0.0.1 65536>",
            "<updatetracker f 0 18446744073709551616 127.0.0.1 5001>",
            "x" * 2048, None,
        ]
        for wire in cases:
            with self.subTest(wire=wire):
                with self.assertRaises(ValueError):
                    protocol_parse_request(wire)

    def test_invalid_replies(self):
        cases = [
            "", "REP LIST 0", "<>", "<REP LIST>", "<REP LIST -1>",
            "<REP LIST 18446744073709551616>", "<REP LIST END extra>",
            "<createtracker unknown>", "<updatetracker movie>",
            "<updatetracker ../movie succ>", "<REP GET END badmd5>",
            f"<0 movie 1 {MD5}>", f"<1 movie -1 {MD5}>",
            f"<1 ../movie 1 {MD5}>",
            "<REP LIST 0>\n<REP LIST END>\n", "<<REP LIST 0>>",
            "x" * 2048, None,
        ]
        for wire in cases:
            with self.subTest(wire=wire):
                with self.assertRaises(ValueError):
                    protocol_parse_reply(wire)

    def test_invalid_formatter_fields(self):
        requests = [
            None, {}, {"type": "unknown"},
            {"type": "get", "filename": "../file.track"},
            {
                "type": "create", "filename": "f", "filesize": True,
                "description": "-", "md5": MD5, "ip": "127.0.0.1", "port": 5001,
            },
            {
                "type": "create", "filename": "f", "filesize": 1,
                "description": "bad\ninjection", "md5": MD5,
                "ip": "127.0.0.1", "port": 5001,
            },
        ]
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    protocol_format_request(request)

        for reply in (
            None, {}, {"type": "create", "status": "unknown"},
            {"type": "get_end", "md5": "bad"},
            {"type": "list_begin", "number": True},
        ):
            with self.subTest(reply=reply):
                with self.assertRaises(ValueError):
                    protocol_format_reply(reply)


if __name__ == "__main__":
    unittest.main()
