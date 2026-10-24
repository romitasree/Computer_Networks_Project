import socketserver
import threading
import unittest

from peer_download import ChunkError, Peer, TCPChunkClient


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class TransportTests(unittest.TestCase):
    def server(self, responder):
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                responder(self.rfile.readline(512), self.request)

        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return Peer("127.0.0.1", server.server_address[1], 0, 10000, 1)

    def test_fragmented_tcp_header_and_binary_payload(self):
        data = b"<GET invalid>\n\x00\xff"

        def respond(request, sock):
            self.assertEqual(request, f"<GET file 0 {len(data)}>\n".encode())
            for byte in f"<REP GET {len(data)}>\n".encode() + data:
                sock.sendall(bytes([byte]))

        peer = self.server(respond)
        self.assertEqual(TCPChunkClient(1)(peer, "file", 0, len(data)), data)

    def test_reject_error_short_oversize_and_bad_header(self):
        for response in (b"<GET invalid>\n", b"<REP GET 4>\nab", b"<REP GET 4>\nabcde",
                         b"<REP GET 5>\nabcd", b"x" * 200):
            with self.subTest(response=response):
                peer = self.server(lambda request, sock: sock.sendall(response))
                with self.assertRaises(ChunkError):
                    TCPChunkClient(1)(peer, "file", 0, 4)

    def test_peer_timeout(self):
        release = threading.Event()
        self.addCleanup(release.set)
        peer = self.server(lambda request, sock: release.wait(1))
        with self.assertRaises(ChunkError):
            TCPChunkClient(0.02)(peer, "file", 0, 4)

    def test_invalid_chunk_requests_rejected_before_network(self):
        peer = Peer("localhost", 1, 0, 10000, 1)
        for start, end in ((0, 1025), (0, 0), (-1, 10), (2, 1)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                TCPChunkClient()(peer, "file", start, end)


if __name__ == "__main__":
    unittest.main()
