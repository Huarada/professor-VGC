import { createRoot } from 'react-dom/client'
import ReplayViewer from '../src/ReplayViewer'
import type { BattleReplay } from '../src/types'
import replay from './fixtures/bo3-game1-view.json'
import '../src/style.css'

// Visual fixture from the public testProfessor vs Huarada BO3, game 1.
// Run Vite and open /tests/replay-preview.html?turn=3.
const turn = Number(new URLSearchParams(window.location.search).get('turn') || '3')
createRoot(document.getElementById('root')!).render(
  <div className="page-shell"><main className="replay-preview-page"><div className="review-grid">
    <ReplayViewer replay={replay as BattleReplay} initialTurn={turn}/>
    <section className="card answer-card"><p className="eyebrow">Visual test fixture</p><h2>Game 1 · Turn {turn}</h2><p className="muted">The snapshots and action text come from the archived public replay. Advance the timeline to verify sprites, Mega forms, HP, status and action playback.</p></section>
  </div></main></div>,
)
