'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Generations } = require('@smogon/calc');
const { buildPokemon } = require('../src/calcEngine');
const { attributes } = require('../src/candidateSupport');

const axes = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
const open = () => Object.fromEntries(axes.map(stat => [stat, { minimum: 0, maximum: 32 }]));

test('all six Modest Mega Gengar attributes include every native legal single-axis completion', () => {
  const result = attributes({ species: 'Gengar-Mega', level: 50, natures: ['Modest'], stat_points: open() });
  assert.equal(result.supported, true);
  assert.equal(Object.keys(result.attributes).length, 6);
  assert.deepEqual(result.attributes.spe, { minimum: 150, maximum: 182 });
  assert.ok(result.attributes.spa.maximum > 32); // Final attributes and investment are different units.
  for (const stat of axes) {
    for (let points = 0; points <= 32; points++) {
      const mon = buildPokemon(Generations.get(0), { species: 'Gengar-Mega', nature: 'Modest', level: 50,
        evs: { [stat]: points * 8 } });
      assert.ok(result.attributes[stat].minimum <= mon.rawStats[stat]);
      assert.ok(result.attributes[stat].maximum >= mon.rawStats[stat]);
    }
  }
});

test('a fully declared legal spread yields exact attributes on every axis', () => {
  const points = { hp: 2, atk: 0, def: 0, spa: 32, spd: 0, spe: 32 };
  const result = attributes({ species: 'Gengar-Mega', level: 50, natures: ['Modest'],
    stat_points: Object.fromEntries(axes.map(stat => [stat, { minimum: points[stat], maximum: points[stat] }])) });
  assert.equal(result.supported, true);
  assert.ok(axes.every(stat => result.attributes[stat].minimum === result.attributes[stat].maximum));
  assert.equal(result.attributes.hp.minimum, 137);
  assert.equal(result.attributes.spa.minimum, 244);
  assert.equal(result.attributes.spe.minimum, 182);
});

test('marginal endpoint projection respects the remaining 66-point budget', () => {
  const bounds = open();
  bounds.atk = { minimum: 32, maximum: 32 };
  bounds.spe = { minimum: 32, maximum: 32 };
  const result = attributes({ species: 'Gengar-Mega', level: 50, natures: ['Modest'], stat_points: bounds });
  assert.equal(result.supported, true);
  assert.deepEqual(result.attributes.hp, { minimum: 135, maximum: 137 });
});

test('invalid native points, unknown natures and unsupported species never produce guessed stats', () => {
  const base = { species: 'Gengar-Mega', level: 50, natures: ['Modest'], stat_points: open() };
  for (const request of [
    { ...base, natures: ['Invented'] }, { ...base, species: 'Missing Species' },
    { ...base, level: 100 }, { ...base, stat_points: { ...open(), atk: { minimum: 0, maximum: 256 } } },
  ]) {
    const result = attributes(request);
    assert.equal(result.supported, false);
    assert.ok(result.reason);
    assert.equal(result.attributes, undefined);
  }
});
