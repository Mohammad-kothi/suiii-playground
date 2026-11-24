#!/usr/bin/env python3
"""Suiii Playground local server.

Serves index.html on http://localhost:8787 and talks to GroqCloud with the
API key from .env (or the GROQ_API_KEY environment variable), so the key
never reaches the browser. Also runs web searches and reads pages for the
Web search tool. Standard library only: run it with `python3 server.py`.
"""
import base64
import errno
import html
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8787"))
# GROQ_BASE_URL is only for testing against a stand-in API; real use leaves it unset.
GROQ = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
NON_CHAT = re.compile(r"whisper|orpheus|tts|guard|safeguard|playai|distil", re.I)
PREFERRED = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]


EXAMPLE_KEYS = {"your-key-here", "gsk_paste_your_real_key_here"}
NO_KEY = "No GROQ_API_KEY found. Add it to .env next to server.py."
EXAMPLE_KEY = "The GROQ_API_KEY in .env is still the example text. Paste your real key from console.groq.com/keys."


def load_key():
    key = os.environ.get("GROQ_API_KEY", "").strip()
    path = os.path.join(HERE, ".env")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("GROQ_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
    return key


def key_problem(key):
    if not key:
        return NO_KEY
    if key in EXAMPLE_KEYS:
        return EXAMPLE_KEY
    return ""


def groq_err(e):
    try:
        return json.loads(e.read().decode("utf-8", "replace"))["error"]["message"]
    except Exception:
        return "HTTP %s from Groq" % getattr(e, "code", "?")


def fetch(url, timeout=12, limit=2_000_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        ctype = r.headers.get("Content-Type", "")
        final = r.geturl()
    m = re.search(r"charset=([\w-]+)", ctype)
    try:
        text = raw.decode(m.group(1) if m else "utf-8", "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    return text, ctype, final


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def clean_inline(s):
    # Titles and snippets only have inline tags like <strong>, so drop them without adding spaces.
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def host_of(url):
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def real_bing_url(href):
    p = urllib.parse.urlparse(href)
    if "bing.com" in p.netloc and p.path.startswith("/ck/"):
        u = urllib.parse.parse_qs(p.query).get("u", [""])[0]
        if u.startswith("a1"):
            b = u[2:]
            try:
                return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")
            except Exception:
                return ""
    return href


def bing(q):
    page, _, _ = fetch("https://www.bing.com/search?" + urllib.parse.urlencode({"q": q, "setlang": "en", "cc": "us"}))
    out = []
    for block in page.split('<li class="b_algo"')[1:]:
        block = block[:8000]
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        url = real_bing_url(html.unescape(a.group(1)))
        if not url.startswith(("http://", "https://")) or "bing.com" in host_of(url):
            continue
        if any(r["u"] == url for r in out):
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append({"t": clean_inline(a.group(2))[:160], "u": url, "d": host_of(url),
                    "snippet": clean_inline(p.group(1))[:400] if p else ""})
    return out


def wikipedia(q):
    page, _, _ = fetch("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": 6}))
    out = []
    for r in json.loads(page).get("query", {}).get("search", []):
        title = r["title"]
        out.append({"t": title, "u": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                    "d": "en.wikipedia.org", "snippet": clean(r.get("snippet", ""))})
    return out


def public_host(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def page_text(page):
    title_m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|aside|iframe)\b[^>]*>.*?</\1>", " ", page)
    body = re.sub(r"(?is)<!--.*?-->", " ", body)
    body = re.sub(r"(?i)<(br|/p|/div|/h[1-6]|/li|/tr|/section|/article)\b[^>]*>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return (clean(title_m.group(1)) if title_m else ""), text


class Handler(BaseHTTPRequestHandler):
    server_version = "Suiii/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s %s\n" % (self.command, self.path.split("?")[0], args[1] if len(args) > 1 else ""))

    def send_json(self, code, obj):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def allowed(self):
        # Only answer this page: right host (blocks DNS rebinding) and a custom
        # header other websites can't send without a CORS preflight we never allow.
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        ok_origin = origin in (None, "http://localhost:%d" % PORT, "http://127.0.0.1:%d" % PORT)
        return host in ("localhost", "127.0.0.1") and ok_origin and self.headers.get("X-Kiln") == "1"

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self.static()
        if not u.path.startswith("/api/"):
            return self.send_json(404, {"error": "Not found"})
        if not self.allowed():
            return self.send_json(403, {"error": "Forbidden"})
        if u.path == "/api/models":
            return self.models()
        if u.path == "/api/search":
            return self.search(q.get("q", [""])[0])
        if u.path == "/api/read":
            return self.read(q.get("url", [""])[0])
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/api/chat":
            return self.send_json(404, {"error": "Not found"})
        if not self.allowed():
            return self.send_json(403, {"error": "Forbidden"})
        self.chat()

    def static(self):
        with open(os.path.join(HERE, "index.html"), "rb") as f:
            b = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def models(self):
        key = load_key()
        if key_problem(key):
            return self.send_json(401, {"error": key_problem(key), "code": "no_key"})
        req = urllib.request.Request(GROQ + "/models", headers={"Authorization": "Bearer " + key, "User-Agent": "suiii-playground"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            return self.send_json(e.code, {"error": groq_err(e)})
        except Exception as e:
            return self.send_json(502, {"error": "Couldn't reach Groq: %s" % e})
        models = [{"id": m["id"], "owner": m.get("owned_by"), "ctx": m.get("context_window"), "maxOut": m.get("max_completion_tokens")}
                  for m in data.get("data", []) if m.get("active", True) and not NON_CHAT.search(m["id"])]
        rank = lambda m: (PREFERRED.index(m["id"]) if m["id"] in PREFERRED else len(PREFERRED), -(m["ctx"] or 0), m["id"])
        self.send_json(200, {"models": sorted(models, key=rank)})

    def chat(self):
        key = load_key()
        if key_problem(key):
            return self.send_json(401, {"error": key_problem(key), "code": "no_key"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 12_000_000:
            return self.send_json(413, {"error": "Request too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "Invalid JSON"})
        body["stream"] = True
        req = urllib.request.Request(GROQ + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "suiii-playground"})
        try:
            upstream = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            return self.send_json(e.code, {"error": groq_err(e)})
        except Exception as e:
            return self.send_json(502, {"error": "Couldn't reach Groq: %s" % e})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for line in upstream:
                self.wfile.write(line)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page stopped the reply
        finally:
            upstream.close()

    def search(self, q):
        q = q.strip()[:200]
        if not q:
            return self.send_json(400, {"error": "Empty query"})
        engine, results = "Bing", []
        try:
            results = bing(q)
        except Exception as e:
            sys.stderr.write("bing failed: %s\n" % e)
        if not results:
            engine = "Wikipedia"
            try:
                results = wikipedia(q)
            except Exception as e:
                sys.stderr.write("wikipedia failed: %s\n" % e)
        self.send_json(200, {"engine": engine, "results": results[:8]})

    def read(self, url):
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return self.send_json(400, {"error": "Only http and https links can be read"})
        if not public_host(p.hostname):
            return self.send_json(400, {"error": "That address isn't on the public internet"})
        try:
            page, ctype, final = fetch(url, limit=1_500_000)
        except urllib.error.HTTPError as e:
            return self.send_json(502, {"error": "The page returned HTTP %s" % e.code})
        except Exception as e:
            return self.send_json(502, {"error": "Couldn't load the page: %s" % e})
        if "html" in ctype or not ctype:
            title, text = page_text(page)
        elif ctype.startswith("text/") or "json" in ctype:
            title, text = "", page
        else:
            return self.send_json(415, {"error": "Not a text page (%s)" % ctype.split(";")[0]})
        self.send_json(200, {"title": title, "url": final, "text": text[:9000]})


def main():
    if not os.path.exists(os.path.join(HERE, "index.html")):
        sys.exit("index.html is missing next to server.py")
    problem = key_problem(load_key())
    if problem:
        print("Warning: " + problem + " The server rereads .env on every request, so no restart is needed after you fix it.")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        sys.exit("Port %d is already in use, probably by Suiii Playground running in another window.\n"
                 "Open http://localhost:%d to use it, stop the other copy first, or start this one on another port:\n"
                 "  PORT=%d python3 server.py" % (PORT, PORT, PORT + 1))
    print("Suiii Playground running at http://localhost:%d  (Ctrl+C to stop)" % PORT, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
