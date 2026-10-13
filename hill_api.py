"""
Tiny live API for Hill study room and library availability.

reserve.reslife.ucla.edu sends no CORS headers, so the site can't read it from
the browser. This server scrapes it (scrape_hill.collect) and returns the same
JSON as hill.json with CORS enabled, so the page can refresh on every open.
/library does the same for UCLA Library hours and study rooms
(scrape_library.collect, same JSON as library.json).

Results are cached for CACHE_SECONDS (the booking site itself sends
max-age=60) and concurrent requests share one scrape. Data up to MAX_STALE old
is served immediately while a fresh scrape runs in the background; the JSON's
"updated" field always says how old it is.

    GET /hill    -> hill.json structure, plus "live": true
    GET /library -> library.json structure, plus "live": true
    GET /health  -> "ok"

Run locally:  python hill_api.py   (PORT env var, default 8000)
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scrape_hill import collect as collect_hill
from scrape_library import collect as collect_library

CACHE_SECONDS = int(os.environ.get('CACHE_SECONDS', 60))
DAYS = int(os.environ.get('DAYS', 8))
ALLOW_ORIGIN = os.environ.get('ALLOW_ORIGIN', '*')

MAX_STALE = int(os.environ.get('MAX_STALE', 15 * 60))
# Library hours and rooms change slower and take ~17 requests to read, so cache longer.
LIBRARY_CACHE_SECONDS = int(os.environ.get('LIBRARY_CACHE_SECONDS', 5 * 60))


class Feed:
    """One scraped feed with its own cache, so /hill and /library don't wait on each other."""

    def __init__(self, collect, cache_seconds=CACHE_SECONDS):
        self.collect = collect
        self.cache_seconds = cache_seconds
        self.lock = threading.Lock()         # guards cache
        self.refreshing = threading.Lock()   # held while a scrape runs
        self.cache = {'at': 0.0, 'body': None}

    def refresh(self):
        """Scrape once and store the result. Only one scrape runs at a time."""
        with self.refreshing:
            with self.lock:  # another thread may have just finished one
                if self.cache['body'] is not None and time.time() - self.cache['at'] <= self.cache_seconds:
                    return
            data = self.collect(DAYS)
            data['live'] = True
            body = json.dumps(data, separators=(',', ':')).encode()
            with self.lock:
                self.cache['body'], self.cache['at'] = body, time.time()

    def refresh_in_background(self):
        def run():
            try:
                self.refresh()
            except Exception as e:
                print(f"background refresh failed: {e}", flush=True)
        if not self.refreshing.locked():
            threading.Thread(target=run, daemon=True).start()

    def get(self):
        """Return (json bytes, age in seconds).

        Fresh (under cache_seconds): served from cache. Stale but under MAX_STALE:
        served immediately while a background refresh runs, because the booking
        site can take a minute to answer. Older or empty: wait for a scrape.
        """
        with self.lock:
            body, at = self.cache['body'], self.cache['at']
        age = time.time() - at
        if body is not None and age <= self.cache_seconds:
            return body, int(age)
        if body is not None and age <= max(MAX_STALE, self.cache_seconds):
            self.refresh_in_background()
            return body, int(age)
        self.refresh()
        with self.lock:
            return self.cache['body'], int(time.time() - self.cache['at'])


FEEDS = {'/hill': Feed(collect_hill), '/library': Feed(collect_library, LIBRARY_CACHE_SECONDS)}


def get_hill():
    return FEEDS['/hill'].get()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype='application/json', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', ALLOW_ORIGIN)
        self.send_header('Cache-Control', f'public, max-age={CACHE_SECONDS}')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', ALLOW_ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0].rstrip('/')
        if path in ('', '/health'):
            return self._send(200, b'ok', 'text/plain')
        if path in FEEDS:
            try:
                body, age = FEEDS[path].get()
            except Exception as e:  # never leak a stack trace to clients
                print(f"scrape failed: {e}", flush=True)
                return self._send(502, json.dumps({'error': 'upstream unavailable'}).encode())
            return self._send(200, body, extra={'Age': str(age)})
        self._send(404, json.dumps({'error': 'not found'}).encode())

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f"hill_api listening on :{port}", flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
