import { useEffect, useMemo, useState } from 'react'
import type { BattleReplay, ReplayPokemon, ReplaySnapshot } from './types'

type Side = 'p1' | 'p2'
type Target = { side: Side; species: string }
type TurnCue = { side: Side; species: string; move: string; detail: string; targets: Target[] }

const sides: Side[] = ['p1', 'p2']
const fieldRows: Side[] = ['p2', 'p1'] // opponent (front sprites) above, p1 (back sprites) below, as in Showdown
const spriteRoot = 'https://play.pokemonshowdown.com/sprites'

function spriteIds(name: string): string[] {
  const parts = name.toLowerCase().replace(/[^a-z0-9-]/g, '').split('-').filter(Boolean)
  if (!parts.length) return []
  return [...new Set([parts.join('-'), `${parts[0]}-${parts.slice(1).join('')}`, parts.join('')])]
}

function spriteCandidates(species: string, forme: string, back: boolean, still: boolean): { url: string; baseFallback: boolean }[] {
  const animated = back ? 'ani-back' : 'ani'
  const staticFolder = back ? 'gen5-back' : 'gen5'
  const folders = still
    ? [[staticFolder, 'png'], ['gen5', 'png']]
    : [[animated, 'gif'], [staticFolder, 'png'], ['ani', 'gif'], ['gen5', 'png']]
  const names = [...new Set([...(forme ? spriteIds(forme) : []), ...spriteIds(species)])]
  return names.flatMap(id => folders.map(([folder, ext]) => ({
    url: `${spriteRoot}/${folder}/${id}.${ext}`,
    baseFallback: Boolean(forme) && !spriteIds(forme).includes(id),
  })))
}

function PokemonSprite({ species, forme = '', back, still }: {
  species: string; forme?: string; back: boolean; still: boolean
}) {
  const candidates = useMemo(() => spriteCandidates(species, forme, back, still), [species, forme, back, still])
  const [candidate, setCandidate] = useState(0)
  useEffect(() => setCandidate(0), [candidates])
  if (candidate >= candidates.length) {
    return <img className="pokemon-sprite sprite-placeholder" src="/favicon.svg" alt="" aria-hidden="true" draggable={false} />
  }
  const selected = candidates[candidate]
  return <>
    <img className="pokemon-sprite" src={selected.url} alt="" aria-hidden="true" draggable={false}
      onError={() => setCandidate(previous => previous + 1)} />
    {selected.baseFallback && <small className="sprite-fallback">Base sprite</small>}
  </>
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return reduced
}

function turnCues(frame: ReplaySnapshot, replay: BattleReplay): TurnCue[] {
  return frame.log.flatMap(line => {
    const match = /^(p[12]) (.+?) used (.+?)(?:\s+[—–]\s+(.+))?$/u.exec(line)
    if (!match) return []
    const side = match[1] as Side
    const opposite: Side = side === 'p1' ? 'p2' : 'p1'
    const detail = match[4] || ''
    const roster = [...new Set([...(replay.team[opposite] || []), ...Object.keys(frame.pokemon[opposite] || {})])]
    const targets = roster
      .filter(species => {
        const name = species.toLowerCase()
        const text = detail.toLowerCase()
        return text.includes(name) || (name.includes('-') && text.includes(name.split('-')[0]))
      })
      .slice(0, 2)
      .map(species => ({ side: opposite, species }))
    return [{ side, species: match[2], move: match[3], detail, targets }]
  })
}

function samePokemon(display: string, action: string): boolean {
  const clean = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '')
  return clean(display) === clean(action) || clean(display).startsWith(`${clean(action)}mega`)
}

function stateFor(frame: ReplaySnapshot, side: Side, species: string): ReplayPokemon | undefined {
  return frame.pokemon[side]?.[species]
    || Object.values(frame.pokemon[side] || {}).find(mon => samePokemon(mon.species, species) || samePokemon(mon.forme, species))
}

function ActivePokemon({ frame, side, species, slot, cue, cueIndex, still }: {
  frame: ReplaySnapshot; side: Side; species: string; slot: number
  cue: TurnCue | undefined; cueIndex: number; still: boolean
}) {
  const mon = stateFor(frame, side, species)
  const hp = mon?.fainted ? 0 : Math.max(0, Math.min(100, Math.round(mon?.hp_percent ?? 100)))
  const attacker = cue?.side === side && samePokemon(species, cue.species)
  const target = cue?.targets.some(item => item.side === side && samePokemon(species, item.species))
  const boosts = Object.entries(mon?.boosts || {}).filter(([, stage]) => stage !== 0)
  return <div className={`active-mon${attacker ? ' is-attacking' : ''}${target ? ' is-hit' : ''}${mon?.fainted ? ' is-fainted' : ''}`} key={`${side}-${slot}-${species}-${cueIndex}`}>
    <div className="mon-info"><div className="mon-heading"><strong>{mon?.forme || species}</strong><span>{mon?.fainted ? 'Fainted' : `${hp}%`}</span></div>
      <div className="hp-track"><span className={hp <= 20 ? 'hp-critical' : hp <= 50 ? 'hp-warning' : ''} style={{ width: `${hp}%` }} /></div>
      {(mon?.status || boosts.length > 0) && <div className="mon-badges">{mon?.status && <span className="status-badge">{mon.status.toUpperCase()}</span>}{boosts.map(([stat, stage]) => <span className={stage > 0 ? 'boost-positive' : 'boost-negative'} key={stat}>{stat.toUpperCase()} {stage > 0 ? '+' : ''}{stage}</span>)}</div>}
    </div>
    <div className="mon-sprite-stage"><span className="sprite-ground"/><PokemonSprite species={mon?.species || species} forme={mon?.forme || ''} back={side === 'p1'} still={still}/></div>
  </div>
}

export default function ReplayViewer({ replay, initialTurn = 0 }: { replay: BattleReplay; initialTurn?: number }) {
  const [index, setIndex] = useState(() => Math.max(0, replay.snapshots.findIndex(snapshot => snapshot.turn === initialTurn)))
  const [cueIndex, setCueIndex] = useState(-1)
  const [replayPass, setReplayPass] = useState(0)
  const still = useReducedMotion()
  const frames = replay.snapshots
  const frame = frames[Math.min(index, frames.length - 1)]
  const cues = useMemo(() => frame ? turnCues(frame, replay) : [], [frame, replay])
  useEffect(() => setIndex(Math.max(0, replay.snapshots.findIndex(snapshot => snapshot.turn === initialTurn))), [replay, initialTurn])
  useEffect(() => {
    setCueIndex(-1)
    if (!cues.length) return
    if (still) { setCueIndex(0); return }
    const timers = cues.map((_, n) => window.setTimeout(() => setCueIndex(n), 300 + n * 1400))
    return () => timers.forEach(timer => window.clearTimeout(timer))
  }, [cues, replayPass, still])

  if (!frame) return <p className="muted">No turn-by-turn replay is available for this game.</p>
  const cue = cues[cueIndex]
  return <section className="replay-panel" aria-label="Battle replay">
    <div className="section-heading"><div><p className="eyebrow">Battle timeline</p><h3>{frame.turn === 0 ? 'Opening leads' : `Turn ${frame.turn}`}</h3></div><span className="pill">{index + 1} / {frames.length}</span></div>
    <div className="battle-field" key={`field-${index}`}>
      {fieldRows.map(side => <div className={`battle-side ${side}`} key={side}>
        <div className="battle-side-header"><span className="side-flag">{side.toUpperCase()}</span><strong>{replay.player_names[side] || side}</strong></div>
        <div className="active-list">{(frame.active[side] || []).length
          ? frame.active[side].map((species, slot) => <ActivePokemon frame={frame} side={side} species={species} slot={slot} cue={cue} cueIndex={cueIndex} still={still} key={`${side}-${slot}-${species}-${cueIndex}`}/>)
          : <p className="empty-side">No Pokémon active at the end of this turn.</p>}</div>
      </div>)}
    </div>
    {cue && <div className="turn-action" key={`action-${index}-${cueIndex}`}>
      <div className="action-portrait action-attacker"><PokemonSprite species={stateFor(frame, cue.side, cue.species)?.species || cue.species} forme={stateFor(frame, cue.side, cue.species)?.forme || ''} back={cue.side === 'p1'} still={still}/></div>
      <div className="action-copy"><span>LOGGED ACTION {cueIndex + 1} / {cues.length}</span><strong>{cue.species} used {cue.move}</strong>{cue.detail && <small>{cue.detail}</small>}</div>
      {cue.targets.length > 0 && <div className="action-targets">{cue.targets.map(target => <div className="action-portrait action-target" key={`${target.side}-${target.species}`}><PokemonSprite species={stateFor(frame, target.side, target.species)?.species || target.species} forme={stateFor(frame, target.side, target.species)?.forme || ''} back={target.side === 'p1'} still={still}/></div>)}</div>}
    </div>}
    {frame.conditions.length > 0 && <div className="condition-row">{frame.conditions.map((condition, n) => <span className="pill" key={n}>{condition}</span>)}</div>}
    <div className="timeline-controls"><button type="button" onClick={() => setIndex(Math.max(0, index - 1))} disabled={index === 0} aria-label="Previous turn">←</button><input type="range" min="0" max={frames.length - 1} value={index} onChange={event => setIndex(Number(event.target.value))} aria-label="Replay turn"/><button type="button" onClick={() => setIndex(Math.min(frames.length - 1, index + 1))} disabled={index === frames.length - 1} aria-label="Next turn">→</button></div>
    {cues.length > 0 && <button className="secondary-button replay-actions-button" type="button" onClick={() => setReplayPass(pass => pass + 1)}>Replay turn actions</button>}
    <div className="turn-log"><strong>Turn events</strong>{frame.log.length ? frame.log.map((line, n) => <p key={n}>{line}</p>) : <p>Team preview and opening leads.</p>}</div>
  </section>
}
