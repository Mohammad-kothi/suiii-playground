// POST /api/chat: forwards one chat completion to Groq and streams its reply straight back.
import { GROQ, guard, groqKey, groqError, json } from './_lib.js';

export async function POST(request) {
  const denied = guard(request); if (denied) return denied;
  const k = groqKey(); if (k.error) return json(401, { error: k.error, code: 'no_key' });
  const text = await request.text();
  if (text.length > 4_000_000) return json(413, { error: 'This request is too large to send. Remove an attachment or start a new chat.' });
  let body;
  try { body = JSON.parse(text || '{}'); } catch { return json(400, { error: 'Invalid JSON' }); }
  body.stream = true;
  let res;
  try {
    res = await fetch(GROQ + '/chat/completions', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + k.key, 'Content-Type': 'application/json', 'User-Agent': 'suiii-playground' },
      body: JSON.stringify(body),
      signal: request.signal,   // stopping the reply in the page also stops it at Groq
    });
  } catch (e) {
    if (e && e.name === 'AbortError') return new Response(null, { status: 499 });
    return json(502, { error: 'Couldn’t reach Groq: ' + e.message });
  }
  if (!res.ok) return json(res.status, { error: await groqError(res) });
  return new Response(res.body, { status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform' } });
}
