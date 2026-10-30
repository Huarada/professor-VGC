'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Dex } = require('@pkmn/dex');
const { calcDamage, compareSpeed } = require('../src/calcEngine');

const attacker = { species: 'Garchomp' };
const defender = { species: 'Incineroar' };

test('every Z and Max move is refused at the engine boundary', () => {
  const moves = Dex.moves.all().filter(move => move.isZ || move.isMax);
  assert.ok(moves.length > 50);
  for (const move of moves) {
    assert.throws(() => calcDamage(0, attacker, defender, move.name), /Unsupported battle mechanic/);
  }
});

test('foreign set fields, crystals and formes are refused even through direct IPC payloads', () => {
  for (const entry of [
    { teraType: 'Steel' }, { isDynamaxed: true }, { useZ: true },
    { item: 'Groundium Z' }, { battleForme: 'Charizard-Gmax' },
    { species: 'Groudon-Primal' },
  ]) {
    assert.throws(() => calcDamage(0, { ...attacker, ...entry }, defender, 'Dragon Claw'),
      /Only Mega Evolution/);
  }
});

test('missing Champions support never changes damage or speed rules to another game', () => {
  const missing = { species: 'Amoonguss' };
  assert.throws(() => calcDamage(0, attacker, missing, 'Dragon Claw'), /Champions engine/);
  assert.throws(() => compareSpeed(0, attacker, missing), /Champions engine/);
});

test('Mega Evolution remains supported under Champions rules', () => {
  const result = calcDamage(0, { species: 'Charizard', battleForme: 'Charizard-Mega-Y' },
    defender, 'Flamethrower');
  assert.ok(result.maxPercent > 0);
  assert.match(result.desc, /Mega/);
});
