'use strict';

/**
 * Regression tests for smogonDex.js's format-resolution policy: exact
 * match only, no fallback to any other format (see that module's own
 * header comment for the two live bugs, both 2026-08-29, that led here —
 * first an unscoped fallback leaked non-VGC data, then a "any VGC-tagged
 * format" fallback still leaked stale-regulation data, e.g. real VGC
 * teammates from a year-old regulation (`gen9vgc2025`) recommended for a
 * battle under the current one).
 *
 * These specific assertions — "no format supplied means no query is even
 * attempted" — are the guard clauses that make exact-match-only actually
 * hold, and are network-free (they return before ever calling into
 * @pkmn/smogon), so they run with node's own built-in test runner:
 *   node --test test/smogonDex.test.js
 * A live, network-touching check that the exact-format path itself
 * behaves (e.g. a real species/format combination Smogon does publish)
 * belongs in `npm run smoke`, not here — this file stays fast and
 * hermetic.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const dex = require('../src/smogonDex');

test('analyses() with no format returns empty without querying Smogon', async () => {
  assert.deepEqual(await dex.analyses(9, 'Garchomp', undefined), []);
  assert.deepEqual(await dex.analyses(9, 'Garchomp', ''), []);
  assert.deepEqual(await dex.analyses(9, 'Garchomp', null), []);
});

test('sets() with no format returns empty without querying Smogon', async () => {
  assert.deepEqual(await dex.sets(9, 'Garchomp', undefined), []);
  assert.deepEqual(await dex.sets(9, 'Garchomp', ''), []);
});

test('stats() with no format returns null without querying Smogon', async () => {
  assert.equal(await dex.stats(9, 'Garchomp', undefined), null);
  assert.equal(await dex.stats(9, 'Garchomp', ''), null);
});

test('the module no longer exports the removed cross-format fallback helpers', () => {
  // Regression guard: firstVgcFormat/vgcOnly/resolveVgcFormat implemented
  // the "any VGC-tagged format is close enough" policy that turned out to
  // still leak stale-regulation data — they were deliberately deleted,
  // not just stopped being called. If they reappear, exact-match-only has
  // regressed back toward the leaky behavior.
  assert.equal(dex.firstVgcFormat, undefined);
  assert.equal(dex.vgcOnly, undefined);
  assert.equal(dex.resolveVgcFormat, undefined);
});
