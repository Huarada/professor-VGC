'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const support = require('../src/candidateSupport');

function request() {
  return {
    attacker: { species: 'Garchomp', ability: 'Rough Skin', item: '', evs: { atk: 256 }, nature: 'Adamant' },
    defender: { species: 'Incineroar', ability: 'Intimidate', item: '', evs: { hp: 0, def: 0 }, nature: 'Hardy' },
    move: 'Dragon Claw', field: {},
    before: { numerator: 170, denominator: 170, mode: 'exact' },
    attackerBefore: { numerator: 183, denominator: 183, mode: 'exact' },
  };
}

test('versioned Champions catalog uses native investment limits and fixed IVs', () => {
  const rules = support.rules();
  assert.equal(rules.total_budget, 66);
  assert.equal(rules.stat_cap, 32);
  assert.equal(rules.fixed_iv, 31);
  assert.equal(rules.natures.length, 25);
  assert.ok(rules.engine_version);
});

test('projection returns integer roll witnesses and the candidate maximum HP', () => {
  const result = support.project(request());
  assert.equal(result.supported, true);
  assert.equal(result.maximum_hp, 170);
  assert.ok(result.witnesses.length > 1);
  assert.ok(result.witnesses.every(witness => witness.hp_before === 170 && Number.isInteger(witness.damage)));
});

test('public 100 percent HP retains multiple integer HP values', () => {
  const input = request();
  input.before = { numerator: 100, denominator: 100, mode: 'percent' };
  const result = support.project(input);
  assert.deepEqual(new Set(result.witnesses.map(witness => witness.hp_before)), new Set([169, 170]));
});

test('exact maximum HP is a candidate constraint, not an assumed percent', () => {
  const input = request();
  input.before.denominator = 171;
  assert.deepEqual(support.project(input).witnesses, []);
});

test('uncertain multi-hit, survival and variable-power damage are explicitly unsupported', () => {
  for (const move of ['Triple Axel', 'Counter', 'Eruption', 'Body Press', 'Flower Trick']) {
    assert.equal(support.moveSupport(move).supported, false, move);
  }
  const input = request();
  input.defender.item = 'Focus Sash';
  assert.equal(support.project(input).supported, false);
});

test('missing or invalid declared mechanics never silently become a neutral set', () => {
  for (const patch of [{ item: 'Invented Item' }, { ability: 'Invented Ability' }, { evs: { atk: 264 } },
    { evs: { hp: 256, atk: 256, def: 24 } }, { evs: { atk: 4 } }]) {
    const input = request();
    Object.assign(input.attacker, patch);
    assert.equal(support.project(input).supported, false);
  }
});

test('observed Mega form keeps the same spread and uses its real stats', () => {
  const input = request();
  input.attacker = { species: 'Charizard', battleForme: 'Charizard-Mega-Y', ability: 'Blaze', item: 'Charizardite Y' };
  input.attackerBefore = { numerator: 153, denominator: 153, mode: 'exact' };
  input.move = 'Flamethrower';
  assert.equal(support.project(input).supported, true);
  input.attacker.battleForme = 'Charizard-Mega-Unknown';
  assert.equal(support.project(input).supported, false);
});
