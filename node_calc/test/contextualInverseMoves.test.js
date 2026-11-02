'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const support = require('../src/candidateSupport');
const { calcDamage } = require('../src/calcEngine');

function request(move, weather = '', fainted = 0) {
  return {
    attacker: { species: 'Garchomp', ability: 'Rough Skin', item: '', nature: 'Adamant', evs: { atk: 256 } },
    defender: { species: 'Incineroar', ability: 'Intimidate', item: '', nature: 'Hardy' },
    move, field: { weather }, attackerAlliesFainted: fainted,
    before: { numerator: 170, denominator: 170, mode: 'exact' },
    attackerBefore: { numerator: 183, denominator: 183, mode: 'exact' },
  };
}

function damage(input) {
  const projected = support.project(input);
  assert.equal(projected.supported, true, projected.reason);
  const rolls = [...new Set(projected.witnesses.map(witness => witness.damage))].sort((a, b) => a - b);
  const forward = calcDamage(0, input.attacker, input.defender, input.move, input.field,
    100, 100, input.attackerAlliesFainted, null, input.spreadHit || false, input.afterFailedMove || false);
  assert.deepEqual(rolls, [...new Set(forward.damage)].sort((a, b) => a - b));
  return rolls;
}

test('contextual inverse moves use the forward engine across weather and ally-faint states', () => {
  for (const move of ['Weather Ball', 'Solar Beam', 'Last Respects', 'Stone Edge']) {
    assert.equal(support.moveSupport(move).supported, true, move);
    for (const weather of ['', 'Sun', 'Rain', 'Sand', 'Snow']) {
      for (const fainted of [0, 1, 3]) damage(request(move, weather, fainted));
    }
  }
});

test('global auras use native damage semantics without double stacking or unsupported reversal', () => {
  const input = request('Moonblast');
  const ordinary = damage(input);
  const aura = damage({ ...input, field: { isFairyAura: true } });
  assert.ok(Math.min(...aura) > Math.max(...ordinary));
  assert.equal(support.project({ ...input, field: { isFairyAura: true, isAuraBreak: true } }).supported, false);
  const holder = { ...input, attacker: { ...input.attacker, ability: 'Fairy Aura' } };
  assert.deepEqual(damage(holder), damage({ ...holder, field: { isFairyAura: true } }));
  assert.deepEqual(ordinary, damage({ ...input, field: { isDarkAura: true } }));
  const dark = request('Dark Pulse');
  assert.ok(Math.min(...damage({ ...dark, field: { isDarkAura: true } })) > Math.max(...damage(dark)));
});

test('new inverse moves match native contextual forward rolls', () => {
  for (const move of ['Electroweb', 'Discharge', 'Icy Wind', 'Kowtow Cleave', 'Light of Ruin',
    'Low Kick', 'Heat Crash', 'Stomping Tantrum', 'Expanding Force']) {
    for (const field of [{}, { terrain: 'Psychic' }]) {
      for (const spreadHit of [false, true]) {
        for (const afterFailedMove of [false, true]) {
          damage({ ...request(move), field, spreadHit, afterFailedMove });
        }
      }
    }
  }
  const normal = damage(request('Stomping Tantrum'));
  const failed = damage({ ...request('Stomping Tantrum'), afterFailedMove: true });
  assert.ok(Math.min(...failed) > Math.max(...normal));
});

test('weather and prior ally faints change inverse damage instead of using a static power', () => {
  const clearBall = damage(request('Weather Ball'));
  const rainBall = damage(request('Weather Ball', 'Rain'));
  assert.ok(Math.min(...rainBall) > Math.max(...clearBall));
  const clearBeam = damage(request('Solar Beam'));
  const rainBeam = damage(request('Solar Beam', 'Rain'));
  assert.ok(Math.min(...clearBeam) > Math.max(...rainBeam));
  const noFaints = damage(request('Last Respects', '', 0));
  const threeFaints = damage(request('Last Respects', '', 3));
  assert.ok(Math.min(...threeFaints) > Math.max(...noFaints));
  for (const move of ['Beat Up', 'Counter', 'Future Sight', 'Payback']) {
    assert.equal(support.moveSupport(move).supported, false, move);
  }
  for (const fainted of [-1, 4, 1.5]) {
    assert.equal(support.project(request('Last Respects', '', fainted)).supported, false);
  }
});
