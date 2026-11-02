'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Generations, Move, Field, calculate } = require('@smogon/calc');
const { calcDamage, buildPokemon } = require('../src/calcEngine');
const support = require('../src/candidateSupport');

function request(move) {
  return {
    attacker: { species: 'Garchomp', ability: 'Rough Skin', item: '', nature: 'Hardy', evs: { atk: 256, spa: 256 } },
    defender: { species: 'Incineroar', ability: 'Intimidate', item: '', nature: 'Hardy' },
    move, field: {}, before: { numerator: 170, denominator: 170, mode: 'exact' },
    attackerBefore: { numerator: 183, denominator: 183, mode: 'exact' }, spreadHit: false, attackerHitsTaken: 0,
  };
}

test('every Champions catalog move is classified with a reason or complete supported stat semantics', () => {
  const catalog = support.coverage();
  assert.equal(catalog.length, Array.from(Generations.get(0).moves).length);
  assert.equal(new Set(catalog.map(entry => entry.move)).size, catalog.length);
  assert.ok(catalog.filter(entry => entry.supported).length > 150);
  for (const entry of catalog) {
    assert.equal(typeof entry.supported, 'boolean');
    if (!entry.supported) assert.ok(entry.reason, entry.move);
    else {
      assert.ok(['atk', 'def', 'spa'].includes(entry.attack_stat), entry.move);
      assert.ok(['def', 'spd'].includes(entry.defense_stat), entry.move);
      assert.equal(entry.critical_hits, true);
    }
  }
});

test('all supported catalog moves match forward integer rolls across causal contexts', () => {
  const contexts = [
    {}, { field: { weather: 'Rain', terrain: 'Psychic' }, spreadHit: true },
    { field: { weather: 'Sun', defenderSide: { isLightScreen: true, isReflect: true } }, criticalHit: true },
    { field: { isWonderRoom: true }, afterFailedMove: true },
    { attackerBefore: { numerator: 47, denominator: 183, mode: 'exact' } },
  ];
  for (const entry of support.coverage().filter(entry => entry.supported && !entry.multi_hit)) {
    for (const context of contexts) {
      const input = { ...request(entry.move), ...context };
      const inverse = support.project(input);
      assert.equal(inverse.supported, true, `${entry.move}: ${inverse.reason}`);
      const forward = calcDamage(0, input.attacker, input.defender, input.move, input.field, 100,
        input.attackerBefore.numerator / 183 * 100, 0, null, input.spreadHit,
        input.afterFailedMove, false, input.criticalHit, input.attackerHitsTaken);
      assert.deepEqual([...new Set(inverse.witnesses.map(w => w.damage))].sort((a, b) => a-b),
        [...new Set(forward.damage)].sort((a, b) => a-b), entry.move);
    }
  }
});

test('critical rolls ignore adverse attack boosts and beneficial defensive boosts', () => {
  const plain = request('Dragon Claw');
  plain.attacker.boosts = { atk: -4 };
  plain.defender.boosts = { def: 4 };
  plain.criticalHit = true;
  const critical = support.project(plain);
  const neutral = request('Dragon Claw');
  neutral.criticalHit = true;
  assert.deepEqual(critical.witnesses, support.project(neutral).witnesses);
  assert.ok(Math.min(...critical.witnesses.map(w => w.damage)) > Math.max(...support.project({ ...plain, criticalHit: false }).witnesses.map(w => w.damage)));
});

test('observed HP changes Eruption power and full-HP Sash/Sturdy are censored correctly', () => {
  const full = request('Eruption');
  const low = { ...full, attackerBefore: { numerator: 20, denominator: 183, mode: 'exact' } };
  assert.ok(Math.min(...support.project(full).witnesses.map(w => w.damage)) > Math.max(...support.project(low).witnesses.map(w => w.damage)));
  const lethal = request('Close Combat');
  lethal.attacker.evs = { atk: 256 };
  lethal.attacker.boosts = { atk: 6 };
  lethal.defender.item = 'Focus Sash';
  assert.deepEqual(support.project(lethal).witnesses, [{ hp_before: 170, damage: 169 }]);
  const partial = { ...lethal, before: { numerator: 169, denominator: 170, mode: 'exact' } };
  assert.ok(support.project(partial).witnesses.every(w => w.damage > 169));
  lethal.field.magicRoom = true;
  assert.ok(support.project(lethal).witnesses.every(w => w.damage >= 170));
  lethal.field = {};
  lethal.defender.item = '';
  lethal.defender.ability = 'Sturdy';
  assert.deepEqual(support.project(lethal).witnesses, [{ hp_before: 170, damage: 169 }]);
  lethal.attacker.ability = 'Mold Breaker';
  assert.ok(support.project(lethal).witnesses.every(w => w.damage >= 170));
});

test('logged multi-hit damage composes independent rolls and rejects impossible early-KO prefixes', () => {
  const gen = Generations.get(0);
  for (const name of ['Triple Axel', 'Bullet Seed', 'Dual Wingbeat', 'Population Bomb']) {
    const input = request(name);
    input.attacker.ability = 'Pressure';
    input.attacker.evs = { atk: 256 };
    for (const hits of [1, 2]) {
      input.hits = hits;
      const native = calculate(gen, buildPokemon(gen, input.attacker), buildPokemon(gen, input.defender),
        new Move(gen, name, { hits, overrides: { multihit: hits } }), new Field({ gameType: 'Doubles' })).damage;
      const matrix = native.every(Array.isArray) ? native : [native];
      let expected = [0];
      for (const rolls of matrix) expected = [...new Set(expected.flatMap(total => rolls.map(roll => total + roll)))];
      const result = support.project(input);
      assert.equal(result.supported, true, result.reason);
      assert.deepEqual(result.witnesses.map(w => w.damage).sort((a, b) => a-b), expected.sort((a, b) => a-b), `${name} ${hits}`);
    }
  }
  const input = request('Triple Axel');
  input.attacker.ability = 'Pressure';
  input.hits = 3;
  input.before = { numerator: 1, denominator: 170, mode: 'exact' };
  assert.deepEqual(support.project(input).witnesses, []);
  for (const patch of [{ hits: 4 }, { hits: 3, criticalHit: true },
    { hits: 3, defender: { ...input.defender, item: 'Sitrus Berry' } },
    { hits: 3, defender: { ...input.defender, ability: 'Stamina' } }]) {
    assert.equal(support.project({ ...input, ...patch }).supported, false);
  }
});

test('Rage Fist uses the observed received-hit counter and saturates at the Showdown cap', () => {
  const gen = Generations.get(0);
  for (const counter of [0, 1, 3, 6, 10]) {
    const input = { ...request('Rage Fist'), attackerHitsTaken: counter };
    const expected = calculate(gen, buildPokemon(gen, input.attacker), buildPokemon(gen, input.defender),
      new Move(gen, 'Rage Fist', { overrides: { basePower: Math.min(350, 50 + 50 * counter) } }),
      new Field({ gameType: 'Doubles' })).damage;
    assert.deepEqual(support.project(input).witnesses.map(w => w.damage), [...new Set(expected)], `${counter} hits`);
  }
  assert.deepEqual(support.project({ ...request('Rage Fist'), attackerHitsTaken: 6 }).witnesses,
    support.project({ ...request('Rage Fist'), attackerHitsTaken: 10 }).witnesses);
  for (const counter of [undefined, null, -1, 1.5]) {
    assert.equal(support.project({ ...request('Rage Fist'), attackerHitsTaken: counter }).supported, false);
  }
});
