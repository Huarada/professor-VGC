'use strict';

// Additional inverse-inference semantics over the SAME Champions damage engine.
const { Generations, Move } = require('@smogon/calc');
const { Dex } = require('@pkmn/dex');
const { calcRolls, buildPokemon, rulesFor } = require('./calcEngine');
const engineVersion = require('@smogon/calc/package.json').version;
const toID = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
// Eligibility policy, not a damage formula. The compact dex omits effect callbacks,
// so absence of a callback cannot prove that a move's inputs are fully modeled.
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
  // Weather and prior ally faints are explicit event-time calculator inputs.
  'weatherball', 'lastrespects', 'solarbeam', 'stoneedge',
  // Constant hits, weight-based power, terrain and previous-move failure are
  // already represented by the same native forward calculator and event state.
  'electroweb', 'discharge', 'icywind', 'kowtowcleave', 'lightofruin',
  'lowkick', 'heatcrash', 'stompingtantrum', 'expandingforce',
  // Audited single-hit mechanics: all conditional inputs below are reconstructed
  // from the event snapshot, never inferred from an assumed competitive set.
  'bodypress', 'foulplay', 'psyshock', 'psystrike', 'secretsword',
  'eruption', 'waterspout', 'flail', 'reversal', 'hardpress',
  'flowertrick', 'frostbreath', 'stormthrow', 'wickedblow',
  'grassknot', 'heavyslam', 'storedpower', 'powertrip', 'punishment',
  'hex', 'infernalparade', 'barbbarrage', 'facade', 'venoshock',
  'acrobatics', 'smellingsalts', 'terrainpulse', 'risingvoltage',
  'knockoff', 'brickbreak', 'psychicfangs', 'mistyexplosion', 'gravapple', 'solarblade',
  'temperflare', 'ragingbull', 'aurawheel',
  // Ordinary attacks with no missing power/order/counter inputs.
  'aerialace', 'aircutter', 'aquajet', 'aquatail', 'astonish', 'attackorder',
  'bite', 'blazekick', 'branchpoke', 'breakingswipe',
  'brutalswing', 'bugbite', 'bugbuzz', 'bulldoze', 'bulletseed',
  'chillingwater', 'confusion', 'crosschop', 'crosspoison', 'cut',
  'darkestlariat', 'dig', 'dive', 'drainingkiss', 'dragonbreath', 'dragonhammer',
  'dragonrush', 'dragontail', 'drillpeck', 'drillrun', 'dualwingbeat',
  'earthpower', 'ember', 'explosion', 'extrasensory', 'feint', 'firefang',
  'firepunch', 'flamecharge', 'flamewheel', 'flash', 'flipturn', 'focusblast',
  'forcepalm', 'gigadrain', 'gigaimpact',
  'gunkshot', 'hammerarm', 'headcharge', 'headsmash', 'headbutt',
  'highhorsepower', 'highjumpkick', 'hurricane', 'hyperbeam', 'hyperfang',
  'icefang', 'icehammer', 'icepunch', 'iceshard', 'iciclecrash', 'incinerate',
  'infestation', 'ironhead', 'irontail', 'leafstorm', 'leechlife',
  'liquidation', 'lunge', 'machpunch', 'magicalleaf', 'megahorn', 'metalclaw',
  'moonblast', 'mudshot', 'mudslap', 'mysticalfire', 'nightslash', 'nuzzle',
  'petalblizzard', 'petaldance', 'pinmissile', 'pollenpuff', 'powergem',
  'psychicnoise', 'psychocut', 'quickattack', 'razorleaf', 'razorshell',
  'rockblast', 'rocktomb', 'sandssearstorm', 'scald', 'scaleshot',
  'seedbomb', 'selfdestruct', 'shadowclaw', 'shadowsneak', 'signalbeam',
  'skittersmack', 'skyuppercut', 'slackoff', 'slam', 'sludge', 'snarl',
  'snipeshot', 'spark', 'spiritbreak', 'steelwing', 'stompingtantrum',
  'strength', 'strugglebug', 'superpower', 'swift',
  'takedown', 'thunderfang', 'thunderpunch', 'trailblaze', 'triattack',
  'vacuumwave', 'visegrip', 'waterfall', 'watergun', 'waterpulse',
  'wavecrash', 'wingattack', 'woodhammer', 'zenheadbutt',
  'accelerock', 'acidspray', 'alluringvoice', 'ancientpower', 'appleacid',
  'aquacutter', 'aquastep', 'armorcannon', 'axekick', 'beakblast', 'belch',
  'bind', 'bitterblade', 'bittermalice', 'blastburn', 'bounce', 'burnup',
  'burningjealousy', 'ceaselessedge', 'chargebeam', 'circlethrow', 'clangingscales',
  'clearsmog', 'covet', 'crabhammer', 'crushclaw', 'direclaw', 'doubleshock',
  'drumbeating', 'dynamicpunch', 'eeriespell', 'fellstinger', 'fierydance',
  'firelash', 'firespin', 'firstimpression', 'fly', 'flyingpress', 'focuspunch',
  'freezedry', 'frenzyplant', 'gigatonhammer', 'glaiverush', 'grassyglide',
  'headlongrush', 'hornleech', 'hydrocannon', 'icespinner', 'inferno', 'jawlock',
  'jetpunch', 'lastresort', 'lavaplume', 'lowsweep', 'luminacrash', 'makeitrain',
  'matchagotcha', 'megakick', 'meteorassault', 'mortalspin', 'mountaingale',
  'nightdaze', 'overdrive', 'paraboliccharge', 'phantomforce', 'pluck', 'poisonfang',
  'pounce', 'psyshieldbash', 'pyroball', 'ragingfury', 'rapidspin', 'rockwrecker',
  'saltcure', 'sandtomb', 'scorchingsands', 'shadowpunch', 'skyattack', 'slash',
  'smackdown', 'smartstrike', 'snaptrap', 'snore', 'sparklingaria', 'spiritshackle',
  'steelbeam', 'steelroller', 'stoneaxe', 'struggle', 'supercellslam', 'syrupbomb',
  'thief', 'thrash', 'throatchop', 'torchsong', 'triplearrows', 'tropkick',
  'upperhand', 'uproar', 'volttackle', 'whirlpool', 'wrap', 'zapcannon', 'zingzap',
  'anchorshot', 'astralbarrage', 'bloodmoon', 'hyperdrill', 'revelationdance',
  'electroball', 'gyroball', 'shellsidearm', 'fling', 'ragefist',
]);
const HP_POWER = new Set(['eruption', 'waterspout', 'flail', 'reversal']);
const FIXED_DAMAGE = new Set(['seismictoss', 'nightshade', 'dragonrage', 'sonicboom', 'finalgambit', 'superfang']);
const MULTI_HIT = new Set(['tripleaxel', 'iciclespear', 'rockblast', 'bulletseed', 'dualwingbeat',
  'doublehit', 'populationbomb', 'pinmissile', 'scaleshot', 'bonemerang', 'twinbeam', 'tripledive',
  'bonerush', 'tailslap', 'watershuriken', 'geargrind']);
const MULTI_ABILITIES = new Set(['', 'none', 'noability', 'pressure', 'skilllink', 'technician',
  'adaptability', 'compoundeyes', 'keeneye', 'infiltrator', 'intimidate', 'innerfocus',
  'scrappy', 'moldbreaker', 'defiant', 'competitive', 'serenegrace', 'flowerveil',
  'goodasgold', 'fairyaura', 'darkaura', 'sheerforce']);
const REQUIRED_CONTEXT = {
  assurance: 'Requires whether this target took damage earlier in the turn',
  avalanche: 'Requires whether the attacker was damaged earlier in the turn',
  beatup: 'Requires eligible party members and their individual base Attack values',
  comeuppance: 'Requires the causal source and amount of previously received damage',
  counter: 'Requires the causal source, category and amount of previously received damage',
  dragondarts: 'Requires per-target routing of the two darts',
  electroshot: 'Requires charge-stage chronology without double-applying the native pre-hit boost',
  endeavor: 'Requires coupled attacker/defender current HP and nonstandard damage support',
  ficklebeam: 'Requires the observed random double-power activation',
  futuresight: 'Requires delayed-hit attribution and the applicable cast/impact state',
  lashout: 'Requires whether a stat was lowered this turn, not current net boosts',
  metalburst: 'Requires the causal source and amount of previously received damage',
  meteorbeam: 'Requires charge-stage chronology without double-applying the native pre-hit boost',
  mirrorcoat: 'Requires the causal source, category and amount of previously received damage',
  payback: 'Requires observed target action order rather than an assumed Speed comparison',
  round: 'Requires prior partner move ordering and same-turn power chaining',
  spitup: 'Requires the Stockpile counter and its boost attribution',
  boltbeak: 'Requires observed target action/switch order rather than an assumed Speed comparison',
  fishiousrend: 'Requires observed target action/switch order rather than an assumed Speed comparison',
};

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
  const native = Generations.get(0).moves.get(toID(name));
  if (!native || move.isZ || move.isMax) {
    return { supported: false, reason: 'Move is outside the Champions calculator catalog', category: move.category || '' };
  }
  if (!move.exists || !['Physical', 'Special'].includes(move.category)) {
    return { supported: false, reason: 'No direct damaging move', category: move.category || '' };
  }
  if (REQUIRED_CONTEXT[toID(name)]) {
    return { supported: false, reason: REQUIRED_CONTEXT[toID(name)], category: move.category };
  }
  if (move.multihit && !MULTI_HIT.has(toID(name))) {
    return { supported: false, reason: 'Multi-hit damage needs per-hit state and roll composition', category: move.category };
  }
  if (move.ohko) return { supported: false, reason: 'OHKO moves do not constrain offensive or defensive stats', category: move.category };
  if (!DIRECT_MOVES.has(toID(name)) && !FIXED_DAMAGE.has(toID(name)) && !MULTI_HIT.has(toID(name))) {
    return { supported: false, reason: 'Move requires an unaudited counter, order, delayed or runtime mechanic', category: move.category };
  }
  const physical = move.category === 'Physical';
  const built = new Move(Generations.get(0), name);
  return { supported: true, reason: '', category: move.category, critical_hits: true,
    attack_source: toID(name) === 'foulplay' ? 'defender' : 'attacker',
    attack_stat: toID(name) === 'bodypress' ? 'def' : physical ? 'atk' : 'spa',
    defense_stat: built.overrideDefensiveStat === 'def' || physical ? 'def' : 'spd',
    stat_independent: FIXED_DAMAGE.has(toID(name)),
    multi_hit: !!move.multihit,
    uses_speed: ['electroball', 'gyroball'].includes(toID(name)),
    category_from_stats: toID(name) === 'shellsidearm',
    requires_hit_counter: toID(name) === 'ragefist',
    uses_attacker_hp: HP_POWER.has(toID(name)) || toID(name) === 'finalgambit' };
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
    const fainted = request.attackerAlliesFainted || 0;
    if (toID(request.move) === 'lastrespects' && (!Number.isInteger(fainted) || fainted < 0 || fainted > 3)) {
      return { supported: false, reason: 'Ally-faint count is outside modeled bring-four battles' };
    }
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
    if (support.multi_hit) {
      const data = Dex.moves.get(request.move);
      const maximumHits = Array.isArray(data.multihit) ? data.multihit[1] : data.multihit;
      if (!Number.isInteger(request.hits) || request.hits < 1 || request.hits > maximumHits) {
        return { supported: false, reason: 'Multi-hit inference requires the observed legal hit count' };
      }
      if (request.criticalHit || [attacker, defender].some(mon => mon.item || !MULTI_ABILITIES.has(toID(mon.ability)))) {
        return { supported: false, reason: 'Multi-hit items, reactive abilities or critical mixtures need per-hit state modeling' };
      }
    }
    if (request.field?.isAuraBreak || attacker.hasAbility('Aura Break') || defender.hasAbility('Aura Break')) {
      return { supported: false, reason: 'Aura Break is not modeled by the Champions calculator' };
    }
    if ([attacker, defender].some(mon => mon.hasAbility('Parental Bond', 'Disguise'))
        || defender.hasItem('Focus Band') || attacker.hasItem('Metronome')) {
      return { supported: false, reason: 'Multi-hit or survival mechanics require additional support' };
    }
    const maximum = defender.maxHP();
    const witnesses = new Map();
    for (const before of possibleHP(request.before, maximum)) {
      for (const attackerHP of possibleHP(request.attackerBefore, attacker.maxHP())) {
        const damageRolls = calcRolls(0, request.attacker, request.defender, request.move, request.field,
          before / maximum * 100, attackerHP / attacker.maxHP() * 100,
          request.attackerAlliesFainted, request.hits, request.spreadHit, request.afterFailedMove, request.criticalHit, request.attackerHitsTaken);
        for (const damage of damageRolls) {
          const itemActive = !request.field?.magicRoom && !defender.hasAbility('Klutz');
          const sash = itemActive && defender.hasItem('Focus Sash');
          const sturdy = defender.hasAbility('Sturdy') && (!attacker.hasAbility('Mold Breaker')
            || (itemActive && defender.hasItem('Ability Shield')));
          const applied = before === maximum && (sash || sturdy)
            ? Math.min(damage, maximum - 1) : damage;
          // Full-HP survival is a censored observed hit, not the original roll.
          witnesses.set(`${before}:${applied}`, { hp_before: before, damage: applied });
        }
      }
    }
    return { supported: true, maximum_hp: maximum, witnesses: Array.from(witnesses.values()) };
  } catch (error) {
    return { supported: false, reason: `Champions projection unavailable: ${error.message}` };
  }
}

function attributes(request) {
  try {
    const { gen } = rulesFor(0, [{ species: request.species }]);
    if (request.level !== 50) throw new Error('Only level-50 Champions attributes are supported');
    const axes = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
    const bounds = request.stat_points;
    if (!bounds || Object.keys(bounds).length !== 6 || axes.some(stat => !bounds[stat]
        || !Number.isInteger(bounds[stat].minimum) || !Number.isInteger(bounds[stat].maximum)
        || bounds[stat].minimum < 0 || bounds[stat].maximum > 32
        || bounds[stat].minimum > bounds[stat].maximum)) throw new Error('Illegal native stat-point bounds');
    const minimumTotal = axes.reduce((sum, stat) => sum + bounds[stat].minimum, 0);
    if (minimumTotal > 66) throw new Error('Minimum spread exceeds the native budget');
    if (!Array.isArray(request.natures) || !request.natures.length || request.natures.length > 25
        || request.natures.some(nature => !gen.natures.get(toID(nature)))) throw new Error('Unrecognized nature');
    const result = {};
    const low = Object.fromEntries(axes.map(stat => [stat, bounds[stat].minimum * 8]));
    for (const stat of axes) {
      const maximum = Math.min(bounds[stat].maximum, 66 - minimumTotal + bounds[stat].minimum);
      const high = { ...low, [stat]: maximum * 8 };
      const values = [];
      for (const nature of request.natures) {
        for (const evs of [low, high]) {
          // Card attributes: nature applies; item, ability, status and boosts do not.
          const mon = buildPokemon(gen, { species: request.species, level: 50, nature, evs });
          values.push(mon.rawStats[stat]);
        }
      }
      result[stat] = { minimum: Math.min(...values), maximum: Math.max(...values) };
    }
    const formeAbility = /-Mega(?:-[XYZ])?$/i.test(request.species)
      ? buildPokemon(gen, { species: request.species }).ability : null;
    return { species: request.species, level: 50, supported: true, attributes: result, forme_ability: formeAbility,
      engine_version: engineVersion, basis: 'unboosted_outer_bounds', units: 'level_50_attribute_values' };
  } catch (error) {
    return { species: request.species || '', supported: false, reason: error.message };
  }
}

function coverage() {
  return Array.from(Generations.get(0).moves, move => ({ move: move.name, ...moveSupport(move.name) }));
}

module.exports = { rules, moveSupport, project, attributes, coverage };
