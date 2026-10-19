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
const { Dex } = require('@pkmn/dex');

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

// @smogon/calc only recognizes its own weather/terrain names ('Sun', 'Rain',
// 'Electric', ...); anything else is silently ignored — which is exactly how
// Showdown protocol ids like 'SunnyDay' used to reach the engine and drop the
// weather from every calc. The Python side sends in-game names; the Showdown
// ids are accepted here too as a defensive alias.
const WEATHER = {
  sun: 'Sun', sunnyday: 'Sun', rain: 'Rain', raindance: 'Rain', sand: 'Sand',
  sandstorm: 'Sand', snow: 'Snow', snowscape: 'Snow', hail: 'Hail',
  harshsunshine: 'Harsh Sunshine', desolateland: 'Harsh Sunshine',
  heavyrain: 'Heavy Rain', primordialsea: 'Heavy Rain',
  strongwinds: 'Strong Winds', deltastream: 'Strong Winds',
};
const TERRAIN = {
  electric: 'Electric', electricterrain: 'Electric', grassy: 'Grassy',
  grassyterrain: 'Grassy', psychic: 'Psychic', psychicterrain: 'Psychic',
  misty: 'Misty', mistyterrain: 'Misty',
};
const SIDE_FLAGS = [
  'isTailwind', 'isReflect', 'isLightScreen', 'isAuroraVeil', 'isHelpingHand', 'isFriendGuard',
];
const SPREAD_TARGETS = new Set(['allAdjacent', 'allAdjacentFoes']);
const QP_ABILITIES = new Set(['protosynthesis', 'quarkdrive']);

// Usage data (Smogon Chaos) names abilities, items and natures by id
// ('lifeorb', 'scrappy', 'jolly'), but @smogon/calc only matches display
// names and SILENTLY ignores anything else — every back-filled ability and
// item used to drop out of every calc. Resolve every name through the dex.
// Chaos also uses ids for "no item" / "no ability".
const NO_ITEM = new Set(['', 'nothing', 'none', 'noitem']);
const NO_ABILITY = new Set(['', 'none', 'noability']);
// A Mega / Primal has exactly one ability of its own, whatever the base form
// had (revealed in the log or back-filled from usage data).
const FIXED_ABILITY_FORME = /-(Mega|Primal)\b/;

// Moves @smogon/calc cannot project, which it reports as "0% (immune)".
// Reaction moves deal damage from what the user took (Counter, Mirror Coat,
// ...) or from an HP gap (Endeavor): no honest projection exists, so they are
// refused and the caller treats them as "not projected". HP-ratio moves halve
// the target's CURRENT HP, which is exact and computed here.
const REACTION_MOVES = new Set(['counter', 'mirrorcoat', 'metalburst', 'comeuppance', 'bide', 'endeavor']);
const HALF_HP_MOVES = new Set(['superfang', 'ruination', 'naturesmadness']);

function typeImmune(gen, attacker, defender, move) {
  if (['Normal', 'Fighting'].includes(move.type) && attacker.hasAbility('Scrappy', "Mind's Eye")) {
    return false;
  }
  const type = gen.types.get(toID(move.type));
  return !!type && defender.types.some((t) => type.effectiveness[t] === 0);
}

function halfHpResult(gen, attacker, defender, move) {
  const maxHp = defender.maxHP();
  const immune = typeImmune(gen, attacker, defender, move);
  const dmg = immune ? 0 : Math.max(1, Math.floor(defender.curHP() / 2));
  const pct = maxHp ? Math.round((dmg / maxHp) * 1000) / 10 : 0;
  const head = `${attacker.name} ${move.name} vs. ${defender.name}: ${dmg} (${pct}%) -- `;
  return {
    damage: [dmg],
    minPercent: pct,
    maxPercent: pct,
    koChanceText: '',
    isKoGuaranteed: false,
    desc: head + (immune
      ? 'this move has no effect on the target (immune).'
      : "halves the target's current HP"),
  };
}

// Mimikyu's intact Disguise takes the first damaging hit (then it busts and
// loses 1/8 of its max HP, a separate effect); @smogon/calc does not model it.
// The caller sends the forme AT that move: "Mimikyu" = disguise still up.
const MOLD_BREAKERS = ['Mold Breaker', 'Teravolt', 'Turboblaze'];

function disguiseHolds(attacker, defender, move) {
  return defender.name === 'Mimikyu' && defender.hasAbility('Disguise')
    && move.category !== 'Status' && !attacker.hasAbility(...MOLD_BREAKERS);
}

function disguiseResult(attacker, defender, move) {
  return {
    damage: [0],
    minPercent: 0,
    maxPercent: 0,
    koChanceText: '',
    isKoGuaranteed: false,
    desc: `${attacker.name} ${move.name} vs. ${defender.name}: 0 (0%) -- `
      + "Disguise absorbs the hit (Mimikyu then loses 1/8 of its max HP).",
  };
}

// Pokemon Champions (generation 0 in @smogon/calc) invests STAT POINTS, 0-32
// per stat, one point = one stat point at level 50. The Python side keeps every
// spread in the EV scale (usage data's stat points x 8), so convert back here.
const CHAMPIONS_GEN = 0;
const SV_GEN = 9;
const EVS_PER_STAT_POINT = 8;
const MAX_STAT_POINTS = 32;

// The rules one calc runs under. A Pokemon or move missing from the Champions
// data (a regulation newer than this @smogon/calc) falls back to Scarlet/Violet
// rules for that calc, and says so, rather than failing it.
function rulesFor(genNum, specs, moveName) {
  if (genNum !== CHAMPIONS_GEN) return { gen: Generations.get(genNum), note: '' };
  const champions = Generations.get(CHAMPIONS_GEN);
  const missing = specs
    .filter((spec) => spec && !champions.species.get(toID(spec.species)))
    .map((spec) => spec.species);
  if (moveName && !champions.moves.get(toID(moveName))) missing.push(moveName);
  if (!missing.length) return { gen: champions, note: '' };
  return {
    gen: Generations.get(SV_GEN),
    note: ` [Scarlet/Violet rules: ${missing.join(', ')} not in the Pokemon Champions data]`,
  };
}

function toStatPoints(evs) {
  const points = {};
  for (const [stat, value] of Object.entries(evs || {})) {
    points[stat] = Math.min(MAX_STAT_POINTS, Math.round(value / EVS_PER_STAT_POINT));
  }
  return points;
}

function canonicalName(table, name) {
  const entry = table.get(toID(name));
  return entry ? entry.name : name;
}

// @smogon/calc's species lookup only matches the lowercased/stripped `id`
// form (e.g. "charizardmegay"), not display names like "Charizard-Mega-Y" —
// this is the one reliable way to tell "genuinely unknown forme" (undefined)
// apart from "known, just needs the right casing".
function speciesResolves(gen, name) {
  return !!gen.species.get(toID(name));
}

function buildSide(sideSpec) {
  const side = {};
  for (const flag of SIDE_FLAGS) {
    if (sideSpec && sideSpec[flag]) side[flag] = true;
  }
  return side;
}

function buildField(fieldSpec) {
  const spec = fieldSpec || {};
  // This project is VGC-only (always Doubles). @smogon/calc only applies the
  // standard 0.75x spread-move damage reduction (and other doubles-specific
  // mechanics, e.g. Follow Me/redirection-aware moves) when the field is
  // explicitly told it's a Doubles battle — the default (Singles) silently
  // overstates every spread move's damage by ~33% (e.g. Earthquake/Rock
  // Slide/Heat Wave hitting 2 targets).
  return new Field({
    gameType: 'Doubles',
    weather: WEATHER[toID(spec.weather)] || undefined,
    terrain: TERRAIN[toID(spec.terrain)] || undefined,
    isGravity: !!spec.gravity,
    isMagicRoom: !!spec.magicRoom,
    isWonderRoom: !!spec.wonderRoom,
    attackerSide: buildSide(spec.attackerSide),
    defenderSide: buildSide(spec.defenderSide),
  });
}

function buildPokemon(gen, spec) {
  if (!spec || !spec.species) {
    throw new Error('Pokemon spec requires a "species" field');
  }
  const options = {};
  if (spec.level != null) options.level = spec.level;
  if (!NO_ABILITY.has(toID(spec.ability))) options.ability = canonicalName(gen.abilities, spec.ability);
  if (!NO_ITEM.has(toID(spec.item))) options.item = canonicalName(gen.items, spec.item);
  if (spec.nature) options.nature = canonicalName(gen.natures, spec.nature);
  if (spec.teraType) options.teraType = spec.teraType;
  if (spec.evs) options.evs = gen.num === CHAMPIONS_GEN ? toStatPoints(spec.evs) : spec.evs;
  if (spec.ivs) options.ivs = spec.ivs;
  if (spec.status) options.status = spec.status;
  // Stat stage modifiers (-6..+6) ACTIVE at the exact moment this spec
  // represents — e.g. {atk: -1} from an observed Intimidate. @smogon/calc's
  // Pokemon.stats getter applies these automatically to both the damage
  // calc (atk/def/spa/spd) and getFinalSpeed's own base (spe) — one option,
  // both effects. The caller (Python's TurnReplaySimulator) is the one that
  // knows the real, point-in-time value; this is a pure pass-through.
  if (spec.boosts) options.boosts = spec.boosts;
  // @smogon/calc only applies Protosynthesis / Quark Drive when boostedStat is
  // set: 'auto' lets it check Sun / Electric Terrain / Booster Energy itself and
  // pick the highest stat, exactly as the game does. Unset, they never activate.
  if (QP_ABILITIES.has(toID(spec.ability))) options.boostedStat = 'auto';
  // Fainted Pokemon on this side: Supreme Overlord's power boost.
  if (spec.alliesFainted) options.alliesFainted = spec.alliesFainted;
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
  if (FIXED_ABILITY_FORME.test(species)) {
    const forme = gen.species.get(toID(species));
    const own = forme && forme.abilities && forme.abilities[0];
    if (own) options.ability = own;
  }
  const pokemon = new Pokemon(gen, species, options);
  if (spec.hpPercent == null) return pokemon;
  // Current HP (damage already taken this game), so the KO-chance text
  // reflects the real remaining HP instead of assuming a full-HP target.
  const maxHP = pokemon.maxHP();
  const curHP = Math.max(1, Math.min(maxHP, Math.round((maxHP * spec.hpPercent) / 100)));
  return new Pokemon(gen, species, { ...options, curHP });
}

function calcDamage(
  genNum, attackerSpec, defenderSpec, moveName, fieldSpec, defenderHpPercent,
  attackerHpPercent, attackerAlliesFainted,
) {
  const { gen, note } = rulesFor(genNum, [attackerSpec, defenderSpec], moveName);
  const result = damageUnder(
    gen, attackerSpec, defenderSpec, moveName, fieldSpec, defenderHpPercent,
    attackerHpPercent, attackerAlliesFainted,
  );
  return note ? { ...result, desc: result.desc + note } : result;
}

function damageUnder(
  gen, attackerSpec, defenderSpec, moveName, fieldSpec, defenderHpPercent,
  attackerHpPercent, attackerAlliesFainted,
) {
  const fainted = Math.max(0, Math.min(5, attackerAlliesFainted || 0));
  // The attacker's CURRENT HP drives Eruption / Water Spout / Dragon Energy,
  // Flail / Reversal and pinch abilities; without it they all assumed full HP.
  const attacker = buildPokemon(gen, {
    ...attackerSpec, hpPercent: attackerHpPercent, alliesFainted: fainted,
  });
  const defender = buildPokemon(gen, { ...defenderSpec, hpPercent: defenderHpPercent });
  // @smogon/calc 0.12 has no Last Respects scaling: 50 power plus 50 per
  // fainted ally (at most 300). Without it every Last Respects was 50 power.
  const lastRespectsPower = toID(moveName) === 'lastrespects' ? 50 + 50 * fainted : null;
  const moveOptions = lastRespectsPower ? { overrides: { basePower: lastRespectsPower } } : {};
  const move = new Move(gen, moveName, moveOptions);
  if (REACTION_MOVES.has(toID(moveName))) {
    throw new Error(
      `${move.name} is not projected: its damage depends on the damage the user took`,
    );
  }
  if (HALF_HP_MOVES.has(toID(moveName))) return halfHpResult(gen, attacker, defender, move);
  if (disguiseHolds(attacker, defender, move)) return disguiseResult(attacker, defender, move);
  const field = buildField(fieldSpec);

  const result = calculate(gen, attacker, defender, move, field);

  let damage = result.damage;
  if (!Array.isArray(damage)) {
    damage = damage != null ? [damage] : [];
  }
  const flatDamage = damage.flat ? damage.flat(Infinity) : damage;

  const maxHp = defender.maxHP();
  // A multi-hit move (Triple Axel, Population Bomb, Parental Bond) reports one
  // roll array PER HIT: the damage dealt is the sum over the hits, not the
  // extremes of the flattened list (which was a single hit's damage).
  const perHit = damage.length > 0 && damage.every(Array.isArray) ? damage : [flatDamage];
  const minDmg = flatDamage.length ? perHit.reduce((sum, hit) => sum + Math.min(...hit), 0) : 0;
  const maxDmg = flatDamage.length ? perHit.reduce((sum, hit) => sum + Math.max(...hit), 0) : 0;

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
    if (lastRespectsPower) desc = desc.replace('Last Respects', `Last Respects (${lastRespectsPower} BP)`);
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
  const { gen } = rulesFor(genNum, [attackerSpec, defenderSpec]);
  const attacker = buildPokemon(gen, attackerSpec);
  const defender = buildPokemon(gen, defenderSpec);
  const spec = fieldSpec || {};
  const field = buildField(spec);
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

// Guaranteed (100%) Speed stages a move removes from its target (@pkmn/dex data).
function speedDropStages(dexMove) {
  const own = dexMove.boosts && dexMove.boosts.spe;
  if (own && own < 0 && dexMove.target !== 'self') return -own;
  const sec = dexMove.secondary;
  if (sec && sec.chance === 100 && sec.boosts && sec.boosts.spe < 0) return -sec.boosts.spe;
  return 0;
}

function speedControlKind(dexMove) {
  if (dexMove.sideCondition === 'tailwind') return 'tailwind';
  if (dexMove.pseudoWeather === 'trickroom') return 'trick_room';
  if (speedDropStages(dexMove) > 0) return 'speed_drop';
  const sec = dexMove.secondary;
  if (dexMove.status === 'par' || (sec && sec.chance === 100 && sec.status === 'par')) {
    return 'paralysis';
  }
  return '';
}

// Static move data. Category and target come from @smogon/calc itself (the
// same dex the damage calc uses); the Protect-family and speed-control flags
// come from @pkmn/dex, which carries the move effects @smogon/calc's trimmed
// data doesn't expose.
function moveInfo(genNum, name) {
  const { gen } = rulesFor(genNum, [], name);
  if (!gen.moves.get(toID(name))) {
    return { name, known: false, category: '', isSpread: false };
  }
  const move = new Move(gen, name);
  // @pkmn/dex has no Champions generation; move effects are Scarlet/Violet's.
  const dexMove = Dex.forGen(genNum === CHAMPIONS_GEN ? SV_GEN : genNum).moves.get(name);
  const hasDex = !!(dexMove && dexMove.exists);
  return {
    name: move.name,
    known: true,
    category: move.category,
    isSpread: SPREAD_TARGETS.has(move.target),
    isProtect: hasDex && !!dexMove.stallingMove,
    speedControl: hasDex ? speedControlKind(dexMove) : '',
    speedDropStages: hasDex ? speedDropStages(dexMove) : 0,
  };
}

// Every species name the engine's dex knows (formes included) — used by the
// Python side to recognize Pokemon names in free text (regulation guard).
function speciesNames(genNum) {
  return Array.from(Generations.get(genNum).species, (s) => s.name);
}

module.exports = { calcDamage, compareSpeed, formeResolves, moveInfo, speciesNames };
