import type { RunnerRequest, RunnerTools } from '../../../../shared/runner'

export type MemoryToolScope = NonNullable<RunnerRequest<'tools.execute'>['skill_scope']>

export type DesktopRunnerStatusEvent =
  | { tools: RunnerTools; type: 'runner_ready' | 'running' }
  | { type: 'error' | 'stopped' | 'stopping' }

export type DesktopRunnerPhase = 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'

export interface DesktopRunnerState {
  phase: DesktopRunnerPhase
}

/** 模型派发的一次 Runner 工具调用，按 `callId` 查询、认领调用日志并可单独取消。 */
export interface RunnerCallRequest {
  args: Record<string, unknown>
  callId: string
  name: string
  skillScope?: MemoryToolScope
}

/** Runner 调用结局（PROTOCOL「调用日志与未知结果」）：`failed` 是 Runner 明确报告的工具失败或执行前拒绝；`not_executed` 是请求发出前 Runner 未连接；超时、取消、断连、他处持有与日志判定未知都归 `unknown`。 */
export type RunnerCallOutcome =
  | { error: string; status: 'failed' }
  | { result: unknown; status: 'completed' }
  | { status: 'not_executed' }
  | { status: 'unknown' }

export interface SkillItem {
  category: string
  compatible: boolean
  description?: string
  enabled: boolean
  name: string
  platforms?: null | string[]
}

export interface ToolsetItem {
  enabled: boolean
  id: string
  toolNames: string[]
}

export interface RunnerConfigPatch {
  path: readonly (number | string)[]
  value: unknown
}
