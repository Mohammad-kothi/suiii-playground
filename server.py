#!/usr/bin/env python3
"""Suiii Playground local server.

Serves index.html on http://localhost:8787 and gets replies from Claude Code
(the `claude` CLI, signed in to your Anthropic account) with all of its tools
turned off. Also runs web searches and reads pages for the Web search tool.
Standard library only: run it with `python3 server.py`.
"""
import base64
import errno
import glob
import html
import ipaddress
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8787"))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# ---------------------------------------------------------------------------
# Claude Code
# Replies come from the Claude Code CLI on this Mac, signed in to your own
# Anthropic account. Each request runs `claude -p` with every tool, MCP
# server, hook, plugin and slash command turned off, in an empty folder, so it
# acts as a plain chat model and can't read or change anything on this Mac.
# ---------------------------------------------------------------------------
MODELS = [
    {"id": "claude-opus-5-5", "owner": "Anthropic", "ctx": 1_000_000, "maxOut": 128_000},
    {"id": "claude-sonnet-5-5", "owner": "Anthropic", "ctx": 1_000_000, "maxOut": 128_000},
    {"id": "claude-haiku-5-5", "owner": "Anthropic", "ctx": 1_000_000, "maxOut": 128_000},
    {"id": "claude-fable-5-2", "owner": "Anthropic", "ctx": 1_000_000, "maxOut": 128_000},
]
MODEL_IDS = {m["id"] for m in MODELS}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
DEFAULT_SYSTEM = "You are Claude, a helpful AI assistant made by Anthropic, answering in a chat playground."
DESKTOP_CLI = os.path.expanduser("~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude")
WORKDIR = tempfile.mkdtemp(prefix="suiii-claude-")  # empty folder: nothing for Claude Code to read


def _version_key(path):
    m = re.search(r"claude-code/([\d.]+)/", path)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def find_claude():
    """The Claude Code CLI: $CLAUDE_BIN, then `claude` on PATH, then the copy inside the Claude desktop app."""
    p = os.environ.get("CLAUDE_BIN", "").strip()
    if p and os.access(p, os.X_OK):
        return p
    p = shutil.which("claude")
    if p:
        return p
    for c in ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        c = os.path.expanduser(c)
        if os.access(c, os.X_OK):
            return c
    found = sorted(glob.glob(DESKTOP_CLI), key=_version_key)
    return found[-1] if found else None


def login_command(cli):
    return "claude auth login" if shutil.which("claude") == cli else '"%s" auth login' % cli


def child_env(extra=None):
    env = dict(os.environ)
    if env.get("CLAUDECODE"):
        # Started from inside a Claude Code session: don't pass that session's private
        # settings on, so the CLI uses your own sign-in like it does from Terminal.
        for k in list(env):
            if k.startswith("CLAUDE") or k == "ANTHROPIC_BASE_URL":
                del env[k]
    env.update(extra or {})
    return env


def auth_status(cli):
    r = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30, cwd=WORKDIR,
                       env=child_env(), stdin=subprocess.DEVNULL)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    return json.loads(m.group(0)) if m else {}


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def image_blocks(content):
    out = []
    for p in content if isinstance(content, list) else []:
        url = (p.get("image_url") or {}).get("url", "") if isinstance(p, dict) and p.get("type") == "image_url" else ""
        m = re.match(r"data:(image/(?:png|jpeg|gif|webp));base64,(.+)", url, re.S)
        if m:
            out.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
    return out


def to_prompt(messages):
    """OpenAI-style messages from the page -> (system prompt, user message content blocks)."""
    system = "\n\n".join(text_of(m.get("content")) for m in messages if m.get("role") == "system").strip()
    turns = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]
    last = turns.pop() if turns and turns[-1].get("role") == "user" else {"role": "user", "content": ""}
    latest = text_of(last.get("content"))
    if turns:
        hist = []
        for m in turns:
            tag = {"user": "user", "assistant": "assistant", "tool": "tool_result"}[m["role"]]
            hist.append("<%s>\n%s\n</%s>" % (tag, text_of(m.get("content")).strip(), tag))
        prompt = ("<conversation_so_far>\n%s\n</conversation_so_far>\n\n"
                  "Continue this conversation: write your reply to the user's latest message below. "
                  "Reply directly, without repeating the earlier turns or these tags.\n\n%s") % ("\n".join(hist), latest)
    else:
        prompt = latest
    return system or DEFAULT_SYSTEM, image_blocks(last.get("content")) + [{"type": "text", "text": prompt or "(empty message)"}]


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


class ClaudeRun:
    """Streams one Claude Code reply to the page as OpenAI-style server-sent events."""

    def __init__(self, h, proc, content):
        self.h, self.proc = h, proc
        self.lines = queue.Queue()
        self.err = []
        self.started = False
        self.text = ""
        self.t_first = 0.0
        self.stop = None
        msg = {"type": "user", "message": {"role": "user", "content": content}}
        threading.Thread(target=self._feed, args=(json.dumps(msg) + "\n",), daemon=True).start()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _feed(self, line):
        try:
            self.proc.stdin.write(line.encode("utf-8"))
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def _read(self):
        for raw in self.proc.stdout:
            self.lines.put(raw)
        self.lines.put(None)

    def _read_err(self):
        for raw in self.proc.stderr:
            if sum(map(len, self.err)) < 8000:
                self.err.append(raw.decode("utf-8", "replace"))

    def start(self):
        if not self.started:
            self.started = True
            self.h.send_response(200)
            self.h.send_header("Content-Type", "text/event-stream")
            self.h.send_header("Cache-Control", "no-cache")
            self.h.end_headers()

    def emit(self, obj):
        self.start()
        self.h.wfile.write(b"data: " + json.dumps(obj).encode("utf-8") + b"\n\n")
        self.h.wfile.flush()

    def delta(self, **d):
        if not self.t_first:
            self.t_first = time.time()
        self.emit({"choices": [{"index": 0, "delta": d}]})

    def fail(self, msg, auth=False):
        low = msg.lower()
        status = 401 if auth or "not logged in" in low or "/login" in low else 429 if ("limit" in low or "rate" in low) else 502
        if self.started:
            self.emit({"error": {"message": msg}})
        else:
            self.h.send_json(status, {"error": msg, "code": "no_login" if status == 401 else "claude_error"})

    def run(self):
        auth_failed, result = False, None
        try:
            deadline = time.time() + 900
            while time.time() < deadline:
                try:
                    raw = self.lines.get(timeout=5)
                except queue.Empty:
                    if self.started:  # notice a closed tab while the model is thinking quietly
                        self.h.wfile.write(b": keep-alive\n\n")
                        self.h.wfile.flush()
                    continue
                if raw is None:
                    break
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "stream_event" and not ev.get("parent_tool_use_id"):
                    e = ev.get("event") or {}
                    if e.get("type") == "message_start":
                        self.start()  # the request reached the model, so sign-in is fine
                    elif e.get("type") == "content_block_delta":
                        d = e.get("delta") or {}
                        if d.get("type") == "text_delta" and d.get("text"):
                            self.text += d["text"]
                            self.delta(content=d["text"])
                        elif d.get("type") == "thinking_delta" and d.get("thinking"):
                            self.delta(reasoning=d["thinking"])
                    elif e.get("type") == "message_delta":
                        self.stop = (e.get("delta") or {}).get("stop_reason") or self.stop
                elif t == "assistant" and ev.get("error"):
                    auth_failed = auth_failed or ev["error"] == "authentication_failed"
                elif t == "result":
                    result = ev
                    break
            else:
                return self.fail("Claude Code took longer than 15 minutes and was stopped.")
            if result is None:
                tail = "".join(self.err).strip().splitlines()[-1:] or ["Claude Code stopped without a reply."]
                return self.fail(tail[0])
            if result.get("is_error") or result.get("subtype") != "success":
                msg = result.get("result") or "; ".join(map(str, result.get("errors") or [])) or "Claude Code returned an error."
                return self.fail(str(msg), auth_failed)
            if not self.text and result.get("result"):
                self.delta(content=result["result"])  # no partial chunks came through; send the whole reply
            u = result.get("usage") or {}
            gen = max(0.0, time.time() - self.t_first) if self.t_first else 0.0
            self.emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "length" if (self.stop or result.get("stop_reason")) == "max_tokens" else "stop"}],
                       "usage": {"prompt_tokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                                 "completion_tokens": u.get("output_tokens") or 0,
                                 "completion_tokens_details": {"reasoning_tokens": ((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)},
                                 "completion_time": round(gen, 3)}})
            self.h.wfile.write(b"data: [DONE]\n\n")
            self.h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page stopped the reply
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


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
        cli = find_claude()
        if not cli:
            return self.send_json(503, {"error": "Claude Code wasn't found on this Mac. Install the Claude desktop app or the claude CLI.", "code": "no_cli"})
        try:
            st = auth_status(cli)
        except Exception as e:
            return self.send_json(502, {"error": "Couldn't run Claude Code: %s" % e, "code": "cli_error"})
        if not st.get("loggedIn"):
            cmd = login_command(cli)
            return self.send_json(401, {"error": "Claude Code isn't signed in. Run this once in Terminal, then reload: " + cmd,
                                        "code": "no_login", "login": cmd})
        self.send_json(200, {"models": MODELS, "provider": "claude-code"})

    def chat(self):
        cli = find_claude()
        if not cli:
            return self.send_json(503, {"error": "Claude Code wasn't found on this Mac.", "code": "no_cli"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 12_000_000:
            return self.send_json(413, {"error": "Request too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "Invalid JSON"})
        model = body.get("model") if body.get("model") in MODEL_IDS else MODELS[0]["id"]
        system, content = to_prompt(body.get("messages") or [])
        args = [cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--model", model, "--system-prompt", system,
                "--tools", "", "--strict-mcp-config", "--safe-mode", "--disable-slash-commands", "--no-session-persistence"]
        if body.get("reasoning_effort") in EFFORTS:
            args += ["--effort", body["reasoning_effort"]]
        extra = {}
        max_out = body.get("max_completion_tokens") or body.get("max_tokens")
        if isinstance(max_out, int) and max_out > 0:
            extra["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(min(max_out, 128_000))
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=WORKDIR, env=child_env(extra))
        except OSError as e:
            return self.send_json(502, {"error": "Couldn't start Claude Code: %s" % e, "code": "cli_error"})
        ClaudeRun(self, proc, content).run()

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
    cli = find_claude()
    if not cli:
        print("Warning: Claude Code wasn't found. Install the Claude desktop app or the claude CLI, or set CLAUDE_BIN.")
    else:
        try:
            if not auth_status(cli).get("loggedIn"):
                print("Claude Code isn't signed in yet. Run this once, then reload the page:\n  " + login_command(cli))
        except Exception as e:
            print("Warning: couldn't run Claude Code (%s)" % e)
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
