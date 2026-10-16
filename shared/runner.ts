import type { RunnerOperations } from './api.generated'

export type RunnerMethod = keyof RunnerOperations
export type RunnerRequest<K extends RunnerMethod> = RunnerOperations[K]['request']
export type RunnerResponse<K extends RunnerMethod> = RunnerOperations[K]['response']
export interface RunnerRequestOptions {
  id?: number | string
  timeoutMs?: number
}
export type RunnerRequester = <K extends RunnerMethod>(
  method: K,
  params: RunnerRequest<K>,
  options?: RunnerRequestOptions
) => Promise<RunnerResponse<K>>

export type RunnerTools = RunnerResponse<'tools.list'>['tools']
