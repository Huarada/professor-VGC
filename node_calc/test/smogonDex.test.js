'use strict';

/**
 * Regression test for a live bug (2026-08-29): an official-Smogon lookup
 * for a species with thin/no VGC coverage used to silently fall back to
 * an UNSCOPED query, which could return a totally unrelated format's data
 * (in practice, Smogon's flagship OU singles metagame) — e.g. a
 * team-improvement question got back a real, official-Smogon-sourced
 * "teammate"/"set" that was entirely a singles pick, indistinguishable
 * from genuine VGC data once it reached the LLM's trusted context (see
 * src/smogonDex.js's own header comment for the full story).
 *
 * These are pure, network-free unit tests of the exact filtering logic
 * that regressed — no @pkmn/smogon mocking needed, run with:
 *   node --test test/smogonDex.test.js
 * (Node's own built-in test runner, no new dependency — matches this
 * project's existing "no placeholder code, tests accompany features"
 * convention on the Python side.)
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { firstVgcFormat, vgcOnly } = require('../src/smogonDex');

test('firstVgcFormat picks the VGC-tagged format among others', () => {
  const formats = ['gen9ou', 'gen9vgc2026regmb', 'gen9uu'];
  assert.equal(firstVgcFormat(formats), 'gen9vgc2026regmb');
});

test('firstVgcFormat returns undefined when NOTHING is VGC-tagged — ' +
     'the exact regression: this used to fall back to formats[0] ("gen9ou")', () => {
  const formats = ['gen9ou', 'gen9uu', 'gen9monotype'];
  assert.equal(firstVgcFormat(formats), undefined);
});

test('firstVgcFormat handles an empty or missing list', () => {
  assert.equal(firstVgcFormat([]), undefined);
  assert.equal(firstVgcFormat(undefined), undefined);
});

test('firstVgcFormat is case-insensitive (VGC_HINT is /vgc/i)', () => {
  assert.equal(firstVgcFormat(['gen9OU', 'gen9VGC2026RegMB']), 'gen9VGC2026RegMB');
});

test('vgcOnly keeps only VGC-tagged items, VGC-first order preserved', () => {
  const items = [{ format: 'gen9ou' }, { format: 'gen9vgc2026regmb' }, { format: 'gen9uu' }];
  const result = vgcOnly(items, (i) => i.format);
  assert.deepEqual(result, [{ format: 'gen9vgc2026regmb' }]);
});

test('vgcOnly returns an empty list — not a substitution — when nothing is VGC-tagged', () => {
  const items = [{ format: 'gen9ou' }, { format: 'gen9uu' }];
  assert.deepEqual(vgcOnly(items, (i) => i.format), []);
});
