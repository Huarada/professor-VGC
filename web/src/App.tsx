import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getPreview, getResult, getStatus, importBo3, resetConversation, submit } from './api'
import ReplayViewer from './ReplayViewer'
import type { AnalysisInput } from './api'
import type { FinalResult, GameResult, ImportResult, JobStatus, Mode, Orchestrator, Preview, Provider, Range, Stat } from './types'
import { stats } from './types'

const labels: Record<Stat, string> = { hp: 'HP', atk: 'Atk', def: 'Def', spa: 'SpA', spd: 'SpD', spe: 'Spe' }
const range = (value?: Range) => value ? value.minimum === value.maximum ? `${value.minimum}` : `${value.minimum}–${value.maximum}` : 'Unknown'

function Segmented<T extends string>({ label, options, value, onChange }: {
  label: string; options: { value: T; label: string }[]; value: T; onChange: (value: T) => void
}) {
  return <fieldset className="segmented"><legend>{label}</legend><div className="segment-row">
    {options.map(option => <button key={option.value} type="button" aria-pressed={value === option.value}
      className={value === option.value ? 'selected' : ''} onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div></fieldset>
}

function Pokeball({ size = 28 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="18" fill="#fff" stroke="#1e2a4a" strokeWidth="3"/><path d="M2 20a18 18 0 0 1 36 0Z" fill="#d83d42"/><path d="M2 20h36" stroke="#1e2a4a" strokeWidth="3"/><circle cx="20" cy="20" r="6" fill="#fff" stroke="#1e2a4a" strokeWidth="3"/></svg>
}

// Seed 1674 fixes which side fires first and the 10 s cycle; the artwork is original pixel SVG.
const designSeed = 1674
const bolt = <svg className="shot" viewBox="0 0 8 10" shapeRendering="crispEdges" aria-hidden="true"><path fill="#ffd23f" d="M4 0h3v1H5v2h2v1H5v2h1v1H4v2H3V7H1V6h2V4H1V3h2V1h1z"/></svg>
const orb = <svg className="shot" viewBox="0 0 8 8" shapeRendering="crispEdges" aria-hidden="true"><path fill="#c58bff" d="M2 0h4v1h1v1h1v4H7v1H6v1H2V7H1V6H0V2h1V1h1z"/><path fill="#f2ddff" d="M2 2h2v1H3v1H2z"/></svg>

function AttackRails() {
  return <div className="attack-rails" aria-hidden="true" data-seed={designSeed}>
    <div className="battle-rail battle-rail-left"><div className="sprite-frame"><img className="sprite-animated" src="/sprites/pikachu.gif" alt="" /><img className="sprite-still" src="/sprites/pikachu.png" alt="" /></div>{bolt}</div>
    <div className="battle-rail battle-rail-right"><div className="sprite-frame"><img className="sprite-animated" src="/sprites/mewtwo.gif" alt="" /><img className="sprite-still" src="/sprites/mewtwo.png" alt="" /></div>{orb}</div>
  </div>
}

const roster = ['charizard', 'gengar', 'garchomp', 'lucario', 'metagross', 'gyarados', 'tyranitar', 'volcarona', 'hydreigon']

function HeroBall() {
  return <div className="hero-ball" aria-hidden="true">
    <span className="ball-ring ring-a"><i/></span><span className="ball-ring ring-b"><i/></span>
    <img className="ball-core" src="/pokeball-pixel.svg" alt="" width="132" height="132" />
    <span className="ball-shadow"/>
  </div>
}

function Roster() {
  return <div className="roster" aria-hidden="true"><div className="roster-track">
    {[...roster, ...roster].map((name, index) => <img key={index} src={`/sprites/roster-${name}.gif`} alt="" loading="lazy" />)}
  </div></div>
}

function Inference({ preview }: { preview: Preview }) {
  const inference = preview.inference
  if (!inference) return <p className="muted">Stat-point inference was not enabled for this series.</p>
  const checkpoint = inference.checkpoints.at(-1)
  return <section className="card" aria-label="Stat-point inference">
    <div className="section-heading"><div><p className="eyebrow">Verified calculation · BO3</p><h2>Stat-point inference</h2></div><span className="pill">IVs fixed at 31</span></div>
    <p className="muted">Champions uses 0–32 native points per stat, at most 66 total. Ranges are compatible outer bounds, not a recovered complete spread. Calculated level-50 stats exclude battle modifiers.</p>
    {checkpoint && <><div className="metrics"><span>After game {checkpoint.game}</span><span>{checkpoint.eligible_damage_observations} eligible damage observations</span><span>{checkpoint.complete_local_constraints} certified constraints</span><span>{checkpoint.coverage} search</span></div>
      <div className="table-scroll"><table><thead><tr><th>Pokémon</th><th>Nature</th>{stats.map(stat => <th key={stat}>{labels[stat]}</th>)}</tr></thead><tbody>
        {checkpoint.pokemon.map(mon => <tr key={mon.pokemon_id}><th scope="row">{mon.species}<small>{mon.pokemon_id.split(':slot')[0]}</small></th><td>{mon.natures.join(' / ') || 'Unknown'}</td>{stats.map(stat => <td key={stat}>{range(mon.constrained_native_bounds[stat] || mon.declared_native_bounds[stat])}</td>)}</tr>)}
      </tbody></table></div>
      {checkpoint.pokemon.some(mon => Object.values(mon.range_reductions).some(change => change && change.reduction_percent > 0)) && <div className="reductions"><h3>Intervals narrowed from the initial sheet</h3><ul>{checkpoint.pokemon.flatMap(mon => stats.filter(stat => (mon.range_reductions[stat]?.reduction_percent ?? 0) > 0).map(stat => <li key={`${mon.pokemon_id}-${stat}`}><strong>{mon.species} · {labels[stat]}</strong>: {range(mon.range_reductions[stat]?.baseline)} → {range(mon.range_reductions[stat]?.current)} ({mon.range_reductions[stat]?.reduction_percent}% fewer integer possibilities)</li>))}</ul></div>}
      {checkpoint.pokemon.some(mon => mon.forms.length > 0) && <details><summary>Calculated level-50 attributes</summary><div className="table-scroll"><table><thead><tr><th>Pokémon / form</th>{stats.map(stat => <th key={stat}>{labels[stat]}</th>)}</tr></thead><tbody>{checkpoint.pokemon.flatMap(mon => mon.forms.map(form => <tr key={`${mon.pokemon_id}-${form.form}`}><th scope="row">{form.form}<small>{mon.pokemon_id.split(':slot')[0]}</small></th>{stats.map(stat => <td key={stat}>{range(form.unboosted_attributes[stat])}</td>)}</tr>))}</tbody></table></div></details>}
    </>}
    <details><summary>Logged damage used in the search ({inference.damage_citations.length})</summary><ol className="damage-list">{inference.damage_citations.map((hit, n) => <li key={n}>Game {hit.game}, event {hit.event_index}: {hit.attacker_species} used {hit.move} into {hit.defender_species} ({hit.before.numerator}/{hit.before.denominator} → {hit.after.numerator}/{hit.after.denominator} HP).</li>)}</ol></details>
    {inference.limitations.length > 0 && <details><summary>Calculation limits</summary><ul>{inference.limitations.map((note, n) => <li key={n}>{note}</li>)}</ul></details>}
  </section>
}

function GameReview({ game }: { game: GameResult }) {
  return <div className="review-grid"><ReplayViewer key={game.number} replay={game.replay}/><section className="card answer-card"><p className="eyebrow">Game {game.number} · AI explanation</p><div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{game.answer}</Markdown></div>{game.turn_checks.length > 0 && <details><summary>Turn-by-turn damage checks ({game.turn_checks.length})</summary><div className="raw-evidence"><pre>{JSON.stringify(game.turn_checks, null, 2)}</pre></div></details>}</section></div>
}

export default function App() {
  const [mode, setMode] = useState<Mode>('bo1')
  const [provider, setProvider] = useState<Provider>('gemini')
  const [orchestrator, setOrchestrator] = useState<Orchestrator>('adk')
  const [regulation, setRegulation] = useState('auto')
  const [depth, setDepth] = useState<'low' | 'medium'>('low')
  const [replay, setReplay] = useState('')
  const [importSource, setImportSource] = useState('')
  const [games, setGames] = useState(['', '', ''])
  const [teamsheet, setTeamsheet] = useState('')
  const [question, setQuestion] = useState('')
  const [imported, setImported] = useState<ImportResult | null>(null)
  const [importing, setImporting] = useState(false)
  const [jobId, setJobId] = useState(() => sessionStorage.getItem('pvgc_job_id') || '')
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [result, setResult] = useState<FinalResult | null>(null)
  const [selectedGame, setSelectedGame] = useState(0)
  const [error, setError] = useState('')
  const pendingSubmission = useRef<{ fingerprint: string; key: string } | null>(null)

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    let timer: number
    let fetchedPreview = false
    let fetchedResult = false
    const poll = async () => {
      try {
        const next = await getStatus(jobId)
        if (cancelled) return
        setError('')
        setStatus(next)
        if (next.preview_ready && !fetchedPreview) {
          const verified = await getPreview(jobId)
          if (cancelled) return
          setPreview(verified)
          fetchedPreview = true
        }
        if (next.result_ready && !fetchedResult) {
          const final = await getResult(jobId)
          if (cancelled) return
          setResult(final)
          fetchedResult = true
        }
        if (next.state === 'failed' && next.error) setError(next.error)
        if (next.state === 'queued' || next.state === 'running') timer = window.setTimeout(poll, document.hidden ? 5000 : 1800)
      } catch (cause) { if (!cancelled) { setError(cause instanceof Error ? cause.message : 'Could not load the analysis.'); timer = window.setTimeout(poll, 5000) } }
    }
    void poll()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [jobId])

  const runImport = async () => {
    setError(''); setImporting(true)
    try { const response = await importBo3(importSource); setImported(response); setGames([1, 2, 3].map(n => response.games.find(game => game.number === n)?.payload || '')); setTeamsheet(response.teamsheet) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Import failed.') }
    finally { setImporting(false) }
  }
  const analyze = async () => {
    setError('')
    if (mode === 'bo3') {
      const count = games.filter(game => game.trim()).length
      if (!count || games.slice(0, count).some(game => !game.trim())) {
        setError('Supply BO3 games in order without empty gaps.'); return
      }
    }
    const body: AnalysisInput = { mode, provider, orchestrator, regulation, question,
      replay: mode === 'bo1' ? replay : '', games: mode === 'bo3' ? games.filter(game => game.trim()) : [],
      teamsheet: mode === 'bo3' ? teamsheet || null : null,
      inference: { enabled: true, perspective: 'public', hp_mode: 'percent' }, thinking_level: depth }
    const fingerprint = JSON.stringify(body)
    if (pendingSubmission.current?.fingerprint !== fingerprint) {
      pendingSubmission.current = { fingerprint, key: crypto.randomUUID() }
    }
    try { const next = await submit(body, pendingSubmission.current.key); pendingSubmission.current = null; setPreview(null); setResult(null); setSelectedGame(0); setJobId(next.id); sessionStorage.setItem('pvgc_job_id', next.id); setStatus(next) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not start the analysis.') }
  }
  const reset = async () => {
    try {
      await resetConversation()
      sessionStorage.removeItem('pvgc_job_id')
      setJobId(''); setStatus(null); setPreview(null); setResult(null); setError('')
      pendingSubmission.current = null
      setReplay(''); setGames(['', '', '']); setTeamsheet(''); setQuestion(''); setImported(null)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not reset the conversation.') }
  }
  const activePreview = preview || (result?.mode === 'bo3' ? result.preview : null)
  const activeGame = result?.mode === 'bo3' ? result.games[selectedGame] : result?.game

  return <div className="page-shell"><AttackRails/><header className="site-header"><div className="brand"><Pokeball/><span>Professor<span className="brand-accent">VGC</span></span></div></header>
    <main><div className="intro"><div className="intro-copy"><h1>Find the turn that decided the match.</h1><p>Paste a Showdown replay and ask a question. ProfessorVGC checks every damage roll against the battle log before it explains what happened.</p><p className="facts">Works with best of one and best of three. Mega Evolution is the only special mechanic.</p></div>
      <HeroBall/></div>
      <Roster/>
      <section className="workspace" aria-label="New analysis"><div className="workspace-heading"><div><h2>New analysis</h2></div><span className="pill">1 submission = 1 analysis slot</span></div>
        <div className="controls"><Segmented label="Format" value={mode} onChange={setMode} options={[{ value: 'bo1', label: 'Best of one' }, { value: 'bo3', label: 'Best of three' }]}/><Segmented label="Explanation model" value={provider} onChange={setProvider} options={[{ value: 'gemini', label: 'Gemini' }, { value: 'openai', label: 'OpenAI' }]}/></div>
        <details className="settings"><summary>Settings</summary><div className="settings-grid"><label>Orchestration<select value={orchestrator} onChange={event => setOrchestrator(event.target.value as Orchestrator)}><option value="adk">Google ADK</option><option value="langchain">LangChain</option><option value="native">Native</option></select></label><label>Regulation<input value={regulation} onChange={event => setRegulation(event.target.value)} placeholder="auto, mb, mc"/></label>{mode === 'bo3' && provider === 'gemini' && <Segmented label="Analysis depth" value={depth} onChange={setDepth} options={[{ value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }]}/>}</div><button className="secondary-button" type="button" disabled={status?.state === 'running' || status?.state === 'queued'} onClick={() => void reset()}>Reset conversation</button></details>
        {mode === 'bo1' ? <label className="field-label">Showdown replay URL, JSON or raw log<textarea value={replay} onChange={event => setReplay(event.target.value)} rows={6} placeholder="https://replay.pokemonshowdown.com/gen9championsvgc2026regmc-..."/></label> : <><label className="field-label">Import a BO3 room or replay links<textarea value={importSource} onChange={event => setImportSource(event.target.value)} rows={2} placeholder="Paste the BO3 room URL or up to three replay links"/></label><button className="secondary-button" type="button" onClick={() => void runImport()} disabled={importing || !importSource.trim()}>{importing ? 'Importing…' : 'Import BO3'}</button><p className="hint">Import reads public logs. It does not use an analysis slot or call a model. A 2–0 series needs only two games.</p>{imported && <div className="import-receipt"><strong>{imported.games.length} game(s) imported</strong>{imported.games.map(game => <a key={game.number} href={game.url} target="_blank" rel="noreferrer">Game {game.number} replay ↗</a>)}{imported.warnings.map((warning, n) => <p key={n}>{warning}</p>)}</div>}
          <details className="game-inputs" open={!imported}><summary>Review or paste individual games</summary>{games.map((game, index) => <label className="field-label" key={index}>Game {index + 1}<textarea value={game} onChange={event => setGames(previous => previous.map((entry, n) => n === index ? event.target.value : entry))} rows={2} placeholder={`Game ${index + 1} replay URL, JSON or log`}/></label>)}</details>
          <details className="game-inputs"><summary>Teamsheet and stat-point inference</summary><p className="hint">IVs are fixed at 31. Hidden stat points remain unknown unless battle evidence narrows them.</p><label className="field-label">Teamsheet JSON (optional)<textarea value={teamsheet} onChange={event => setTeamsheet(event.target.value)} rows={4}/></label></details></>}
        <label className="field-label">What would you like to know?<input value={question} onChange={event => setQuestion(event.target.value)} placeholder="What was the winning decision? Which stat-point ranges can the damage support?"/></label>
        <button className="primary-button" type="button" onClick={() => void analyze()} disabled={status?.state === 'running' || status?.state === 'queued' || (mode === 'bo1' ? !replay.trim() : !games.some(Boolean))}>Analyze {mode === 'bo1' ? 'one game' : 'the series'}</button>
      </section>
      {error && <div className="alert error" role="alert">{error}</div>}
      {status && <section className="card status-card" aria-live="polite"><div className="status-icon"><Pokeball size={24}/></div><div><p className="eyebrow">{status.state === 'complete' ? 'Complete' : status.state === 'failed' ? 'Analysis stopped' : 'Analysis in progress'}</p><strong>{status.detail}</strong>{status.state === 'running' && <div className="progress-line"/>}</div></section>}
      {activePreview && <><section className="card"><div className="section-heading"><div><p className="eyebrow">Verified series evidence</p><h2>Series score</h2></div><span className="pill">{activePreview.evidence.complete ? 'Complete series' : 'Incomplete series'}</span></div><div className="score-row">{Object.entries(activePreview.evidence.score).map(([player, score]) => <div key={player}><strong>{score}</strong><span>{player}</span></div>)}</div>{activePreview.evidence.limitations.map((note, n) => <p className="muted" key={n}>{note}</p>)}</section><Inference preview={activePreview}/></>}
      {result && <>{result.mode === 'bo3' && <><section className="card answer-card"><p className="eyebrow">Series explanation</p><div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{result.answer}</Markdown></div></section><div className="game-tabs" role="tablist" aria-label="Review game">{result.games.map((game, index) => <button type="button" role="tab" aria-selected={selectedGame === index} className={selectedGame === index ? 'active' : ''} key={game.number} onClick={() => setSelectedGame(index)}>Game {game.number}</button>)}</div></>}{activeGame && <GameReview game={activeGame}/>}</>}
    </main><footer>ProfessorVGC · Unofficial fan tool · Not affiliated with Nintendo, Game Freak or The Pokémon Company.</footer></div>
}
