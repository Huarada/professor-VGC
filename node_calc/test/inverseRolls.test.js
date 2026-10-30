'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { calcDamage, calcRolls } = require('../src/calcEngine');

test('inverse roll-only evaluation exactly matches the full calculator across causal inputs', () => {
  let checked = 0;
  for (const move of ['Dragon Claw', 'Dragon Pulse', 'Flamethrower']) {
    for (const nature of ['Adamant', 'Modest']) {
      for (const item of ['', 'Life Orb', 'Dragon Fang']) {
        for (const remaining of [100, 35]) {
          const attacker = { species: 'Garchomp', ability: 'Rough Skin', nature, item,
            evs: { atk: 128, spa: 128, spe: 256, hp: 16 }, boosts: { atk: -1 } };
          const defender = { species: 'Incineroar', ability: 'Intimidate', nature: 'Impish', item: '',
            evs: { hp: 256, def: 128, spd: 144 } };
          const args = [0, attacker, defender, move, { weather: 'Sun' }, remaining, 100, 0, null, false, false];
          assert.deepEqual(calcRolls(...args), calcDamage(...args).damage);
          checked++;
        }
      }
    }
  }
  assert.equal(checked, 36);
});

test('nature and declared item affect reverse damage support without changing native caps', () => {
  const attacker = { species: 'Garchomp', ability: 'Rough Skin', nature: 'Adamant', item: '', evs: { atk: 256 } };
  const defender = { species: 'Incineroar', ability: 'Intimidate', nature: 'Impish', item: '', evs: { hp: 256, def: 256 } };
  const rolls = spec => calcRolls(0, spec, defender, 'Dragon Claw', {}, 100, 100, 0, null, false, false);
  assert.ok(Math.min(...rolls({ ...attacker, item: 'Life Orb' })) > Math.max(...rolls(attacker)));
  assert.ok(Math.max(...rolls({ ...attacker, nature: 'Modest' })) < Math.max(...rolls(attacker)));
});
