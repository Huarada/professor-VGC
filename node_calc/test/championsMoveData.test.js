'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { calcDamage } = require('../src/calcEngine');

const evs = { hp: 256, atk: 256, def: 0, spa: 0, spd: 16, spe: 0 };
const defender = { species: 'Dragonite', ability: 'Inner Focus', item: '', nature: 'Jolly', evs: { hp: 256, def: 256, spd: 16 } };
const rolls = (ability, move) => calcDamage(0, { species: 'Pawmot', ability, item: '', nature: 'Jolly', evs }, defender, move,
  {}, null, null, 0, null, false, false, true).damage;

// Champions marks Double Shock as a punching move (Showdown master, data/mods/champions/moves.ts); the
// pokemon-showdown 0.11.11 package used by tools/bo3_sim predates it. ADR-077.
test('Iron Fist boosts Double Shock, which Champions flags as a punching move', () => {
  assert.ok(rolls('Iron Fist', 'Double Shock')[0] > rolls('Volt Absorb', 'Double Shock')[0] * 1.15);
});
