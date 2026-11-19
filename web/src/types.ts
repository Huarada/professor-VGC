export type Mode = 'bo1' | 'bo3'
export type Provider = 'gemini' | 'openai'
export type Orchestrator = 'adk' | 'langchain' | 'native'
export type Range = { minimum: number; maximum: number }
export type Stat = 'hp' | 'atk' | 'def' | 'spa' | 'spd' | 'spe'
export const stats: Stat[] = ['hp', 'atk', 'def', 'spa', 'spd', 'spe']

export type JobStatus = {
  schema_version: number
  id: string
  state: 'queued' | 'running' | 'complete' | 'failed'
  stage: string
  detail: string
  revision: number
  preview_ready: boolean
  result_ready: boolean
  error: string | null
}

export type ReplayPokemon = { species: string; forme: string; hp_percent: number; fainted: boolean; status: string; boosts: Record<string, number> }
export type ReplaySnapshot = {
  turn: number
  active: Record<string, string[]>
  pokemon: Record<string, Record<string, ReplayPokemon>>
  log: string[]
  conditions: string[]
}
export type BattleReplay = {
  player_names: Record<string, string>
  team: Record<string, string[]>
  winner_player: string | null
  snapshots: ReplaySnapshot[]
}
export type GameResult = {
  number: number
  answer: string
  battle_result: string
  regulation: Record<string, unknown> | null
  replay: BattleReplay
  verdicts: Record<string, unknown>[]
  turn_checks: Record<string, unknown>[]
  protect_reads: Record<string, unknown>[]
}
export type PokemonFinding = {
  pokemon_id: string
  species: string
  natures: string[]
  basis: string
  complete_spread_known: boolean
  declared_native_bounds: Partial<Record<Stat, Range>>
  constrained_native_bounds: Partial<Record<Stat, Range>>
  range_reductions: Partial<Record<Stat, { baseline: Range; current: Range; reduction_percent: number }>>
  forms: { form: string; unboosted_attributes: Partial<Record<Stat, Range>> }[]
}
export type Checkpoint = {
  game: number
  coverage: string
  outcome: string
  eligible_damage_observations: number
  complete_local_constraints: number
  pokemon: PokemonFinding[]
}
export type DamageCitation = {
  game: number
  event_index: number
  attacker_species: string
  defender_species: string
  move: string
  before: { numerator: number; denominator: number }
  after: { numerator: number; denominator: number }
}
export type Preview = {
  schema_version: number
  mode: 'bo3'
  evidence: { players: string[]; score: Record<string, number>; complete: boolean; limitations: string[] }
  inference: null | {
    iv_policy: string
    units: string
    perspective: string
    continuity: string
    limitations: string[]
    checkpoints: Checkpoint[]
    damage_citations: DamageCitation[]
  }
}
export type FinalResult =
  | { schema_version: number; mode: 'bo1'; answer: string; game: GameResult }
  | { schema_version: number; mode: 'bo3'; answer: string; preview: Preview; games: GameResult[] }
export type ImportedGame = { number: number; payload: string; url: string; acquisition: string }
export type ImportResult = {
  schema_version: number
  series_id: string | null
  games: ImportedGame[]
  missing_games: number[]
  warnings: string[]
  teamsheet: string
}
