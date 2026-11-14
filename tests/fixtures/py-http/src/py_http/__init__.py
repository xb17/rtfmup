import os
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer


def main():
    port = int(os.environ.get("PORT", "8000"))
    # TCPServer, not HTTPServer: HTTPServer.server_bind calls socket.getfqdn(),
    # a reverse-DNS lookup that stalls for tens of seconds on macOS CI runners.
    TCPServer(("127.0.0.1", port), SimpleHTTPRequestHandler).serve_forever()
