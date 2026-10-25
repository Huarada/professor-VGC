'use strict';

// Additional inverse-inference semantics over the SAME Champions damage engine.
const { Generations } = require('@smogon/calc');
const { Dex } = require('@pkmn/dex');
const { calcRolls, buildPokemon, rulesFor } = require('./calcEngine');
const engineVersion = require('@smogon/calc/package.json').version;
const toID = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
// Eligibility policy, not a damage formula. The compact dex omits effect callbacks,
// so absence of a callback cannot prove that a move has constant power/stat inputs.
const DIRECT_MOVES = new Set([
  'tackle', 'dragonclaw', 'earthquake', 'rockslide', 'heatwave', 'dazzlinggleam',
  'shadowball', 'flamethrower', 'fireblast', 'overheat', 'icebeam', 'blizzard',
  'thunderbolt', 'thunder', 'psychic', 'energyball', 'darkpulse', 'flashcannon',
  'moonblast', 'closecombat', 'drainpunch', 'flareblitz', 'bravebird', 'wildcharge',
  'doubleedge', 'bodyslam', 'hypervoice', 'boomburst', 'airslash', 'aurasphere',
  'dragonpulse', 'hydropump', 'surf', 'muddywater', 'meteormash', 'ironhead',
  'bulletpunch', 'extremespeed', 'fakeout', 'uturn', 'voltswitch', 'suckerpunch',
  'poisonjab', 'sludgebomb', 'sludgewave', 'powerwhip', 'leafblade', 'playrough',
  'crunch', 'outrage', 'xscissor', 'sacredsword', 'dracometeor', 'poltergeist',
]);

function rules() {
  return {
    version: 'champions-stat-points-66-32-v1', engine_version: engineVersion,
    stat_cap: 32, total_budget: 66, fixed_iv: 31,
    natures: Array.from(Generations.get(0).natures, n => ({
      name: n.name, plus: n.plus || '', minus: n.minus || '',
    })),
  };
}

function moveSupport(name) {
  const move = Dex.moves.get(name);
  if (!move.exists || !['Physical', 'Special'].includes(move.category)) {
    return { supported: false, reason: 'No direct damaging move', category: move.category || '' };
  }
  if (move.multihit || move.damage || move.damageCallback || move.ohko || move.willCrit
      || move.basePowerCallback || move.onBasePower || move.overrideOffensiveStat
      || move.overrideDefensiveStat || move.overrideOffensivePokemon || move.overrideDefensivePokemon
      || move.isZ || move.isMax || !move.basePower || !DIRECT_MOVES.has(toID(name))) {
    return { supported: false, reason: 'Variable, critical, multi-hit or nonstandard-stat damage', category: move.category };
  }
  return { supported: true, reason: '', category: move.category };
}

function possibleHP(reading, maximum) {
  if (reading.mode === 'exact') {
    return reading.denominator === maximum ? [reading.numerator] : [];
  }
  const values = [];
  for (let hp = 1; hp <= maximum; hp++) {
    if (Math.ceil(hp * reading.denominator / maximum) === reading.numerator) values.push(hp);
  }
  return values;
}

function project(request) {
  try {
    const support = moveSupport(request.move);
    if (!support.supported) return { supported: false, reason: support.reason };
    const { gen } = rulesFor(0, [request.attacker, request.defender], request.move);
    for (const spec of [request.attacker, request.defender]) {
      if (spec.battleForme && !gen.species.get(toID(spec.battleForme))) {
        return { supported: false, reason: 'Observed forme is missing from Champions engine' };
      }
      if ((spec.item && !gen.items.get(toID(spec.item))) || (spec.ability && !gen.abilities.get(toID(spec.ability)))) {
        return { supported: false, reason: 'Unrecognized declared item or ability' };
      }
      const points = Object.values(spec.evs || {}).map(value => value / 8);
      if (points.some(value => !Number.isInteger(value) || value < 0 || value > 32)
          || points.reduce((sum, value) => sum + value, 0) > 66) {
        return { supported: false, reason: 'Illegal Champions investment' };
      }
    }
    const attacker = buildPokemon(gen, request.attacker);
    const defender = buildPokemon(gen, request.defender);
    if ([attacker, defender].some(mon => mon.hasAbility('Parental Bond', 'Disguise', 'Sturdy'))
        || defender.hasItem('Focus Sash', 'Focus Band')) {
      return { supported: false, reason: 'Multi-hit or survival mechanics require additional support' };
    }
    const maximum = defender.maxHP();
    const witnesses = new Map();
    for (const before of possibleHP(request.before, maximum)) {
      for (const attackerHP of possibleHP(request.attackerBefore, attacker.maxHP())) {
        const damageRolls = calcRolls(0, request.attacker, request.defender, request.move, request.field,
          before / maximum * 100, attackerHP / attacker.maxHP() * 100,
          request.attackerAlliesFainted, null, request.spreadHit, request.afterFailedMove);
        for (const damage of damageRolls) {
          witnesses.set(`${before}:${damage}`, { hp_before: before, damage });
        }
      }
    }
    return { supported: true, maximum_hp: maximum, witnesses: Array.from(witnesses.values()) };
  } catch (error) {
    return { supported: false, reason: `Champions projection unavailable: ${error.message}` };
  }
}

module.exports = { rules, moveSupport, project };
