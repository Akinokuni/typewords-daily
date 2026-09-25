// FSRS 打分器：把「今天学过一遍」写进 TypeWords 的 fsrsData 卡片。
//
// 与 App 完全同一套算法/参数（App 源码 useWordCollectPicker / _id_ 页均用 ts-fsrs）：
//     store.fsrsData[word] = new FSRS(store.fsrsParameters).next(card ?? createEmptyCard(), now, rating).card
//
// stdin : {"params": {...}, "rating": "good|easy|hard|again", "now": "ISO", "items": [["word", cardOrNull], ...]}
// stdout: {"version": "v5.4.2 using FSRS-6.0", "results": {"word": {card...}}, "error": null}
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const tsfsrs = await import('ts-fsrs');
const { FSRS, createEmptyCard, Rating, FSRSVersion } = tsfsrs;

function main() {
  const raw = readFileSync(0, 'utf8');
  const req = JSON.parse(raw || '{}');
  const params = req.params || {};
  const ratingKey = String(req.rating || 'good').toLowerCase();
  const ratingName = ratingKey.charAt(0).toUpperCase() + ratingKey.slice(1);
  if (!(ratingName in Rating) || ratingKey === 'manual') {
    throw new Error(`未知打分: ${req.rating}（可用 again|hard|good|easy）`);
  }
  const now = req.now ? new Date(req.now) : new Date();
  const f = new FSRS(params);
  const results = {};
  for (const [word, card] of req.items || []) {
    const base = card ? { ...card } : createEmptyCard(now);
    if (base.due) base.due = new Date(base.due);
    if (base.last_review) base.last_review = new Date(base.last_review);
    const out = f.next(base, now, Rating[ratingName]);
    const c = out.card;
    results[word] = {
      ...c,
      due: c.due instanceof Date ? c.due.toISOString() : c.due,
      last_review: c.last_review instanceof Date ? c.last_review.toISOString() : c.last_review,
    };
  }
  process.stdout.write(JSON.stringify({
    version: FSRSVersion,
    rating: ratingKey,
    now: now.toISOString(),
    results,
    error: null,
  }));
}

try {
  main();
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String(e && e.message || e), results: {} }));
  process.exit(0);
}