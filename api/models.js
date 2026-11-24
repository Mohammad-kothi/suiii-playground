// GET /api/models: the chat models on this Groq account, best first.
import { GROQ, guard, groqKey, groqError, json } from './_lib.js';

const NON_CHAT = /whisper|orpheus|tts|guard|safeguard|playai|distil/i;
const PREFERRED = ['openai/gpt-oss-120b', 'openai/gpt-oss-20b', 'qwen/qwen3.8-27b'];

export async function GET(request) {
  const denied = guard(request); if (denied) return denied;
  const k = groqKey(); if (k.error) return json(401, { error: k.error, code: 'no_key' });
  let res;
  try { res = await fetch(GROQ + '/models', { headers: { Authorization: 'Bearer ' + k.key, 'User-Agent': 'suiii-playground' }, signal: AbortSignal.timeout(15000) }); }
  catch (e) { return json(502, { error: 'Couldn’t reach Groq: ' + e.message }); }
  if (!res.ok) return json(res.status, { error: await groqError(res) });
  const data = await res.json();
  const models = (data.data || [])
    .filter(m => m.active !== false && !NON_CHAT.test(m.id))
    .map(m => ({ id: m.id, owner: m.owned_by, ctx: m.context_window, maxOut: m.max_completion_tokens }));
  const rank = m => (PREFERRED.includes(m.id) ? PREFERRED.indexOf(m.id) : PREFERRED.length);
  models.sort((a, b) => rank(a) - rank(b) || (b.ctx || 0) - (a.ctx || 0) || a.id.localeCompare(b.id));
  return json(200, { models });
}
