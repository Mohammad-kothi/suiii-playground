// GET /api/search?q=…: web results from Bing, with Wikipedia as a fallback.
import { guard, json, fetchText, cleanInline, clean, hostOf, unescapeHtml } from './_lib.js';

function realBingUrl(href) {
  try {
    const p = new URL(href);
    if (p.host.includes('bing.com') && p.pathname.startsWith('/ck/')) {
      const u = p.searchParams.get('u') || '';
      if (!u.startsWith('a1')) return '';
      const b = u.slice(2).replace(/-/g, '+').replace(/_/g, '/');
      return Buffer.from(b + '='.repeat((4 - (b.length % 4)) % 4), 'base64').toString('utf8');
    }
  } catch { return ''; }
  return href;
}

async function bing(q) {
  const res = await fetchText('https://www.bing.com/search?' + new URLSearchParams({ q, setlang: 'en', cc: 'us' }));
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const page = await res.text();
  const out = [];
  for (const raw of page.split('<li class="b_algo"').slice(1)) {
    const block = raw.slice(0, 8000);
    const a = block.match(/<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/);
    if (!a) continue;
    const url = realBingUrl(unescapeHtml(a[1]));
    if (!/^https?:\/\//.test(url) || hostOf(url).includes('bing.com') || out.some(r => r.u === url)) continue;
    const p = block.match(/<p[^>]*>([\s\S]*?)<\/p>/);
    out.push({ t: cleanInline(a[2]).slice(0, 160), u: url, d: hostOf(url), snippet: p ? cleanInline(p[1]).slice(0, 400) : '' });
  }
  return out;
}

async function wikipedia(q) {
  const res = await fetchText('https://en.wikipedia.org/w/api.php?' + new URLSearchParams({ action: 'query', list: 'search', srsearch: q, format: 'json', srlimit: '6' }));
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  return ((data.query && data.query.search) || []).map(r => ({
    t: r.title, u: 'https://en.wikipedia.org/wiki/' + encodeURIComponent(r.title.replace(/ /g, '_')), d: 'en.wikipedia.org', snippet: clean(r.snippet || ''),
  }));
}

export async function GET(request) {
  const denied = guard(request); if (denied) return denied;
  const q = (new URL(request.url).searchParams.get('q') || '').trim().slice(0, 200);
  if (!q) return json(400, { error: 'Empty query' });
  let engine = 'Bing', results = [];
  try { results = await bing(q); } catch (e) { console.error('bing failed:', e.message); }
  if (!results.length) {
    engine = 'Wikipedia';
    try { results = await wikipedia(q); } catch (e) { console.error('wikipedia failed:', e.message); }
  }
  return json(200, { engine, results: results.slice(0, 8) });
}
