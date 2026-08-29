'use strict';

/**
 * calcEngine.js
 *
 * Thin, dependency-isolated wrapper around @smogon/calc. Everything that knows
 * about the @smogon/calc API surface lives here; calc_server.js only handles
 * transport (stdin/stdout framing). If the calc backend is replaced, only this
 * file changes.
 */

const smogon = require('@smogon/calc');

const { Generations, Pokemon, Move, calculate, Field } = smogon;

// getFinalSpeed accounts for Tailwind, paralysis, Choice Scarf, weather abilities, etc.
let getFinalSpeed = smogon.getFinalSpeed;
if (!getFinalSpeed) {
  try {
    getFinalSpeed = require('@smogon/calc/dist/mechanics/util').getFinalSpeed;
  } catch (_e) {
    getFinalSpeed = null;
  }
}

function toID(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

// @smogon/calc's species lookup only matches the lowercased/stripped `id`
// form (e.g. "charizardmegay"), not display names like "Charizard-Mega-Y" —
// this is the one reliable way to tell "genuinely unknown forme" (undefined)
// apart from "known, just needs the right casing".
function speciesResolves(gen, name) {
  return !!gen.species.get(toID(name));
}

function buildPokemon(gen, spec) {
  if (!spec || !spec.species) {
    throw new Error('Pokemon spec requires a "species" field');
  }
  const options = {};
  if (spec.level != null) options.level = spec.level;
  if (spec.ability) options.ability = spec.ability;
  if (spec.item) options.item = spec.item;
  if (spec.nature) options.nature = spec.nature;
  if (spec.teraType) options.teraType = spec.teraType;
  if (spec.evs) options.evs = spec.evs;
  if (spec.ivs) options.ivs = spec.ivs;
  if (spec.status) options.status = spec.status;
  // Stat stage modifiers (-6..+6) ACTIVE at the exact moment this spec
  // represents — e.g. {atk: -1} from an observed Intimidate. @smogon/calc's
  // Pokemon.stats getter applies these automatically to both the damage
  // calc (atk/def/spa/spd) and getFinalSpeed's own base (spe) — one option,
  // both effects. The caller (Python's TurnReplaySimulator) is the one that
  // knows the real, point-in-time value; this is a pure pass-through.
  if (spec.boosts) options.boosts = spec.boosts;
  // The parser deliberately keeps a Mega-Evolved (or otherwise in-battle
  // forme-changed) Pokemon's stable roster identity in `species` and records
  // the observed appearance separately (battleForme) — so the roster/moveset
  // merge never fragments. Here, at the one place that actually talks to the
  // calc engine, prefer the real observed forme WHEN this installed
  // @smogon/calc version recognizes it; otherwise fall back to the base
  // species exactly as before (safe default for a forme this version's dex
  // genuinely doesn't have yet).
  const species = spec.battleForme && speciesResolves(gen, spec.battleForme)
    ? spec.battleForme
    : spec.species;
  return new Pokemon(gen, species, options);
}

function calcDamage(genNum, attackerSpec, defenderSpec, moveName, fieldSpec) {
  const gen = Generations.get(genNum);
  const attacker = buildPokemon(gen, attackerSpec);
  const defender = buildPokemon(gen, defenderSpec);
  const move = new Move(gen, moveName);
  // This project is VGC-only (always Doubles). @smogon/calc only applies the
  // standard 0.75x spread-move damage reduction (and other doubles-specific
  // mechanics, e.g. Follow Me/redirection-aware moves) when the field is
  // explicitly told it's a Doubles battle — the default (Singles) silently
  // overstates every spread move's damage by ~33% (e.g. Earthquake/Rock
  // Slide/Heat Wave hitting 2 targets).
  const field = new Field({ gameType: 'Doubles', ...(fieldSpec || {}) });

  const result = calculate(gen, attacker, defender, move, field);

  let damage = result.damage;
  if (!Array.isArray(damage)) {
    damage = damage != null ? [damage] : [];
  }
  const flatDamage = damage.flat ? damage.flat(Infinity) : damage;

  const maxHp = defender.maxHP();
  const minDmg = flatDamage.length ? Math.min(...flatDamage) : 0;
  const maxDmg = flatDamage.length ? Math.max(...flatDamage) : 0;

  let koChanceText = '';
  let isKoGuaranteed = false;
  try {
    const ko = result.kochance();
    koChanceText = ko && ko.text ? ko.text : '';
    isKoGuaranteed = !!(ko && ko.chance === 1);
  } catch (_e) {
    koChanceText = '';
  }

  // @smogon/calc's own result.fullDesc()/desc() throw an internal assertion
  // ("damage[damage.length - 1] === 0") specifically on a GUARANTEED-0-damage
  // result (e.g. a type immunity like Fake Out into a Ghost-type, or an
  // ability block) — confirmed live against the real engine. Left uncaught
  // this would crash the calc; caught blindly it used to leave `desc` empty,
  // silently discarding the one human-readable signal for WHY it's 0 right
  // when that explanation matters most for catching a bad move suggestion.
  // maxDmg === 0 with a non-empty damage array (as opposed to an entirely
  // absent computation) is exactly that case, so synthesize the equivalent
  // of Showdown's own "no effect" messaging instead of leaving it blank.
  let desc = '';
  if (flatDamage.length && maxDmg === 0) {
    desc = `${attackerSpec.species} ${moveName} vs. ${defenderSpec.species}: `
      + '0 (0%) -- this move has no effect on the target (immune).';
  } else {
    try {
      desc = result.fullDesc ? result.fullDesc() : '';
    } catch (_e) {
      desc = '';
    }
  }

  return {
    damage: flatDamage,
    minPercent: maxHp ? Math.round((minDmg / maxHp) * 1000) / 10 : 0,
    maxPercent: maxHp ? Math.round((maxDmg / maxHp) * 1000) / 10 : 0,
    koChanceText,
    isKoGuaranteed,
    desc,
  };
}

function effectiveSpeed(gen, mon, field, side) {
  if (getFinalSpeed) {
    try {
      return getFinalSpeed(gen, mon, field, side);
    } catch (_e) {
      // fall through to a manual estimate
    }
  }
  let spe = mon.stats.spe;
  if (side && side.isTailwind) spe *= 2;
  if (mon.status === 'par' && (!mon.ability || mon.ability !== 'Quick Feet')) {
    spe = Math.floor(spe * 0.5);
  }
  if (mon.item === 'Choice Scarf') spe = Math.floor(spe * 1.5);
  return spe;
}

function compareSpeed(genNum, attackerSpec, defenderSpec, fieldSpec) {
  const gen = Generations.get(genNum);
  const attacker = buildPokemon(gen, attackerSpec);
  const defender = buildPokemon(gen, defenderSpec);
  const spec = fieldSpec || {};
  const field = new Field({
    gameType: 'Doubles',
    weather: spec.weather || undefined,
    isGravity: !!spec.gravity,
    attackerSide: { isTailwind: !!spec.attackerTailwind },
    defenderSide: { isTailwind: !!spec.defenderTailwind },
  });
  return {
    attackerSpeed: effectiveSpeed(gen, attacker, field, field.attackerSide),
    defenderSpeed: effectiveSpeed(gen, defender, field, field.defenderSide),
    trickRoom: !!spec.trickRoom,
  };
}

function formeResolves(genNum, name) {
  const gen = Generations.get(genNum);
  return speciesResolves(gen, name);
}

module.exports = { calcDamage, compareSpeed, formeResolves };
