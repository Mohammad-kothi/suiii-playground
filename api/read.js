// GET /api/read?url=…: fetches a public web page and returns its readable text for the model.
import { guard, json, fetchText, publicHost, clean, unescapeHtml } from './_lib.js';

function pageText(page) {
  const title = page.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  let body = page.replace(/<(script|style|noscript|svg|nav|footer|header|form|aside|iframe)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ');
  body = body.replace(/<!--[\s\S]*?-->/g, ' ');
  body = body.replace(/<(br|\/p|\/div|\/h[1-6]|\/li|\/tr|\/section|\/article)\b[^>]*>/gi, '\n');
  let text = unescapeHtml(body.replace(/<[^>]+>/g, ' '));
  text = text.replace(/[ \t\r\f\v]+/g, ' ').replace(/\n\s*\n+/g, '\n\n').trim();
  return [title ? clean(title[1]) : '', text];
}

export async function GET(request) {
  const denied = guard(request); if (denied) return denied;
  let url = new URL(request.url).searchParams.get('url') || '';
  let res;
  // Follow redirects by hand so every hop is checked against private addresses.
  for (let hop = 0; hop < 5; hop++) {
    let p;
    try { p = new URL(url); } catch { return json(400, { error: 'Only http and https links can be read' }); }
    if (!/^https?:$/.test(p.protocol) || !p.hostname) return json(400, { error: 'Only http and https links can be read' });
    if (!(await publicHost(p.hostname))) return json(400, { error: 'That address isn’t on the public internet' });
    try { res = await fetchText(url, { redirect: 'manual' }); }
    catch (e) { return json(502, { error: 'Couldn’t load the page: ' + e.message }); }
    if (res.status >= 300 && res.status < 400 && res.headers.get('location')) { url = new URL(res.headers.get('location'), url).href; continue; }
    break;
  }
  if (!res || (res.status >= 300 && res.status < 400)) return json(502, { error: 'The page redirected too many times' });
  if (!res.ok) return json(502, { error: 'The page returned HTTP ' + res.status });
  const ctype = res.headers.get('content-type') || '';
  const page = (await res.text()).slice(0, 1_500_000);
  let title = '', text = '';
  if (ctype.includes('html') || !ctype) [title, text] = pageText(page);
  else if (ctype.startsWith('text/') || ctype.includes('json')) text = page;
  else return json(415, { error: 'Not a text page (' + ctype.split(';')[0] + ')' });
  return json(200, { title, url, text: text.slice(0, 9000) });
}
