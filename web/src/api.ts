import type { FinalResult, ImportResult, JobStatus, Mode, Orchestrator, Preview, Provider } from './types'

export type AnalysisInput = {
  mode: Mode
  provider: Provider
  orchestrator: Orchestrator
  regulation: string
  question: string
  replay: string
  games: string[]
  teamsheet: string | null
  inference: { enabled: boolean; perspective: string; hp_mode: 'percent' | 'exact' }
  thinking_level: 'low' | 'medium'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string | { msg: string }[] }
    const detail = body.detail
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(x => x.msg).join('; ') : `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

const json = (body: unknown) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const importBo3 = (source: string) => request<ImportResult>('/api/v1/bo3/import', json({ source }))
export const submit = (body: AnalysisInput, key: string) => request<JobStatus>('/api/v1/analyses', {
  ...json(body), headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
})
export const getStatus = (id: string) => request<JobStatus>(`/api/v1/analyses/${id}`)
export const getPreview = (id: string) => request<Preview>(`/api/v1/analyses/${id}/preview`)
export const getResult = (id: string) => request<FinalResult>(`/api/v1/analyses/${id}/result`)
export async function resetConversation(): Promise<void> {
  const response = await fetch('/api/v1/conversation', { method: 'DELETE', credentials: 'same-origin' })
  if (!response.ok) throw new Error('Could not reset the conversation.')
}
