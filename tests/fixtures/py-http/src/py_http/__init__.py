import os
from http.server import HTTPServer, SimpleHTTPRequestHandler


def main():
    port = int(os.environ.get("PORT", "8000"))
    HTTPServer(("127.0.0.1", port), SimpleHTTPRequestHandler).serve_forever()
