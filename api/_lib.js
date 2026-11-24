// Shared helpers for Suiii Playground's Vercel functions.
// Files starting with "_" in /api are not deployed as endpoints.
import { createHash, timingSafeEqual } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import net from 'node:net';

// GROQ_BASE_URL is only for testing against a stand-in API; real deployments leave it unset.
export const GROQ = (process.env.GROQ_BASE_URL || 'https://api.groq.com/openai/v1').replace(/\/+$/, '');
export const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';
const EXAMPLE_KEYS = new Set(['your-key-here', 'gsk_paste_your_real_key_here']);

export const json = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const digest = s => createHash('sha256').update(String(s)).digest();

// Only this page may call the API, and only with the passcode when one is set.
export function guard(request) {
  // A custom header other websites can't send without a CORS preflight, which we never allow.
  if (request.headers.get('x-kiln') !== '1') return json(403, { error: 'Forbidden' });
  const pass = (process.env.SUIII_PASSCODE || '').trim();
  if (!pass) return null;
  const given = (request.headers.get('x-suiii-pass') || '').trim();
  if (!given) return json(401, { error: 'This Suiii site needs a passcode for live Groq answers.', code: 'passcode' });
  if (!timingSafeEqual(digest(given), digest(pass))) return json(401, { error: 'That passcode isn’t right.', code: 'passcode_wrong' });
  return null;
}

export function groqKey() {
  const key = (process.env.GROQ_API_KEY || '').trim();
  if (!key) return { error: 'No GROQ_API_KEY is set for this deployment. Add it in the Vercel project’s environment variables.' };
  if (EXAMPLE_KEYS.has(key)) return { error: 'The GROQ_API_KEY setting is still the example text.' };
  return { key };
}

export async function groqError(res) {
  try { const j = await res.json(); return (j.error && j.error.message) || `HTTP ${res.status} from Groq`; }
  catch { return `HTTP ${res.status} from Groq`; }
}

export async function fetchText(url, { timeout = 12000, limit = 2_000_000, redirect = 'follow' } = {}) {
  const res = await fetch(url, {
    redirect,
    signal: AbortSignal.timeout(timeout),
    headers: { 'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9', Accept: 'text/html,application/xhtml+xml,application/json;q=0.9,text/plain;q=0.8,*/*;q=0.5' },
  });
  return res;
}

const NAMED = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', ndash: '–', mdash: '—', hellip: '…', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', copy: '©', reg: '®', trade: '™', middot: '·', bull: '•' };
export function unescapeHtml(s) {
  return String(s).replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') { const n = e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10); try { return String.fromCodePoint(n); } catch { return m; } }
    return NAMED[e.toLowerCase()] ?? m;
  });
}
export const clean = s => unescapeHtml(String(s).replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
export const cleanInline = s => unescapeHtml(String(s).replace(/<[^>]+>/g, '')).replace(/\s+/g, ' ').trim();
export const hostOf = url => { try { return new URL(url).host.toLowerCase().replace(/^www\./, ''); } catch { return ''; } };

// Refuse private, loopback, link-local and reserved addresses, so the reader can't reach internal services.
function privateIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split('.').map(Number);
    return a === 0 || a === 10 || a === 127 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a >= 224;
  }
  const v = ip.toLowerCase();
  if (v.startsWith('::ffff:')) return privateIp(v.slice(7));
  return v === '::' || v === '::1' || /^f[cd]/.test(v) || /^fe[89ab]/.test(v) || v.startsWith('ff');
}
export async function publicHost(host) {
  const h = host.replace(/^\[|\]$/g, '');
  if (net.isIP(h)) return !privateIp(h);
  try { const addrs = await lookup(h, { all: true }); return addrs.length > 0 && addrs.every(a => !privateIp(a.address)); }
  catch { return false; }
}
