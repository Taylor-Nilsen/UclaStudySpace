"""
Tiny live API for Hill study room and UCLA Library availability.

reserve.reslife.ucla.edu and calendar.library.ucla.edu send no CORS headers,
so the site can't read them from the browser. This server scrapes them
(scrape_hill.collect, scrape_library.collect) and returns the same JSON as
hill.json / library.json with CORS enabled, so the page can refresh on every
open.

Each feed is cached for CACHE_SECONDS (the booking sites send max-age=60) and
concurrent requests share one scrape. Data up to MAX_STALE old is served
immediately while a fresh scrape runs in the background; the JSON's "updated"
field always says how old it is.

    GET /hill     -> hill.json structure, plus "live": true
    GET /library  -> library.json structure, plus "live": true
    GET /health   -> "ok"

Run locally:  python hill_api.py   (PORT env var, default 8000)
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scrape_hill
import scrape_library

CACHE_SECONDS = int(os.environ.get('CACHE_SECONDS', 60))
DAYS = int(os.environ.get('DAYS', 8))
ALLOW_ORIGIN = os.environ.get('ALLOW_ORIGIN', '*')

MAX_STALE = int(os.environ.get('MAX_STALE', 15 * 60))


class Feed:
    """One scraped data source with its own cache and single-flight refresh."""

    def __init__(self, name, scrape):
        self.name = name
        self.scrape = scrape
        self._lock = threading.Lock()          # guards _cache
        self._refreshing = threading.Lock()    # held while a scrape runs
        self._cache = {'at': 0.0, 'body': None}

    def _refresh(self):
        """Scrape once and store the result. Only one scrape runs at a time."""
        with self._refreshing:
            with self._lock:  # another thread may have just finished one
                if self._cache['body'] is not None and time.time() - self._cache['at'] <= CACHE_SECONDS:
                    return
            data = self.scrape()
            data['live'] = True
            body = json.dumps(data, separators=(',', ':')).encode()
            with self._lock:
                self._cache['body'], self._cache['at'] = body, time.time()

    def _refresh_in_background(self):
        def run():
            try:
                self._refresh()
            except Exception as e:
                print(f"{self.name}: background refresh failed: {e}", flush=True)
        if not self._refreshing.locked():
            threading.Thread(target=run, daemon=True).start()

    def get(self):
        """Return (json bytes, age in seconds).

        Fresh (under CACHE_SECONDS): served from cache. Stale but under
        MAX_STALE: served immediately while a background refresh runs, because
        a booking site can take a minute to answer. Older or empty: wait for a
        scrape.
        """
        with self._lock:
            body, at = self._cache['body'], self._cache['at']
        age = time.time() - at
        if body is not None and age <= CACHE_SECONDS:
            return body, int(age)
        if body is not None and age <= MAX_STALE:
            self._refresh_in_background()
            return body, int(age)
        self._refresh()
        with self._lock:
            return self._cache['body'], int(time.time() - self._cache['at'])


FEEDS = {
    '/hill': Feed('hill', lambda: scrape_hill.collect(DAYS)),
    '/library': Feed('library', lambda: scrape_library.collect()),
}


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
                print(f"{path} scrape failed: {e}", flush=True)
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
