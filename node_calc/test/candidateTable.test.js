'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const support = require('../src/candidateSupport');

function base(defender = {}, move = 'Dragon Claw', before = 63) {
  return {
    attacker: { species: 'Garchomp', ability: 'Rough Skin', item: '', evs: {}, nature: 'Adamant' },
    defender: { species: 'Incineroar', ability: 'Intimidate', item: '', evs: {}, nature: 'Hardy', ...defender },
    move, field: {},
    before: { numerator: before, denominator: 100, mode: 'percent' },
    attackerBefore: { numerator: 100, denominator: 100, mode: 'percent' },
  };
}

// Independent per-cell oracle: uncached project() plus the shared display rule.
function oracle(request) {
  const cells = [];
  for (let first = 0; first < 33; first++) {
    for (let second = 0; second < 33; second++) {
      const input = JSON.parse(JSON.stringify(request.base));
      input[request.axes[0].side].evs[request.axes[0].stat] = first * 8;
      input[request.axes[1].side].evs[request.axes[1].stat] = second * 8;
      const result = support.project(input);
      cells.push(!result.supported ? '?' : result.witnesses.some(witness =>
        support.shows(Math.max(0, witness.hp_before - witness.damage), result.maximum_hp, request.after)) ? '1' : '0');
    }
  }
  return cells.join('');
}

const AXES = [{ side: 'defender', stat: 'hp' }, { side: 'defender', stat: 'def' }];

for (const [label, request] of [
  ['partial HP shares rolls across HP investment', { base: base(), axes: AXES }],
  ['Multiscale keys on full HP', { base: base({ species: 'Dragonite', ability: 'Multiscale' }, 'Ice Beam', 100), axes: AXES }],
  ['Hard Press keeps HP in the cache key', { base: base({}, 'Hard Press'), axes: AXES }],
  ['Super Fang keeps HP in the cache key', { base: base({}, 'Super Fang'), axes: AXES }],
]) {
  test(`table equals the uncached per-cell oracle: ${label}`, () => {
    // An after-HP the engine really produces at a mid spread, so cells are mixed.
    const middle = JSON.parse(JSON.stringify(request.base));
    middle.defender.evs = { hp: 128, def: 128 };
    const sample = support.project(middle);
    const witness = sample.witnesses[Math.floor(sample.witnesses.length / 2)];
    const remaining = witness.hp_before - witness.damage;
    const after = { numerator: Math.max(1, Math.floor(remaining * 100 / sample.maximum_hp)), denominator: 100, mode: 'percent' };
    const full = { ...request, after };
    const cells = support.table(full).cells;
    assert.equal(cells.length, 33 * 33);
    assert.ok(cells.includes('1') && cells.includes('0'));
    assert.equal(cells, oracle(full));
  });
}

test('illegal spreads are rejected cells, not unsupported ones', () => {
  const request = base();
  request.defender.evs = { spd: 32 * 8, spe: 32 * 8 };
  const cells = support.table({ base: request, axes: AXES, after: { numerator: 33, denominator: 100, mode: 'percent' } }).cells;
  // hp + def above two points would exceed 66 with 64 already declared.
  for (let hp = 0; hp < 33; hp++) {
    for (let def = 0; def < 33; def++) {
      if (hp + def > 2) assert.equal(cells[hp * 33 + def], '0');
    }
  }
});

test('table rejects malformed axes', () => {
  assert.throws(() => support.table({ base: base(), axes: [], after: base().before }));
  assert.throws(() => support.table({ base: base(), axes: [AXES[0], AXES[0]], after: base().before }));
});

test('candidateBatch dispatch passes only the request to project', () => {
  const input = JSON.stringify({ cmd: 'candidateBatch', requests: [base(), base()] }) + '\n';
  const run = spawnSync(process.execPath, [path.join(__dirname, '..', 'calc_server.js')], { input, encoding: 'utf8' });
  const response = JSON.parse(run.stdout.trim().split('\n')[0]);
  assert.equal(response.ok, true);
  assert.ok(response.result.every(result => result.supported));
});
