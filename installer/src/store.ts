import { atom, computed } from 'nanostores'
import { Channel, invoke } from '@tauri-apps/api/core'

export interface StageInfo {
  name: string
  title: string
}

export type StageState = 'running' | 'succeeded' | 'skipped' | 'failed'

export interface StageRecord {
  state: StageState | null
  durationMs?: number
}

export interface BootstrapStateModel {
  status: 'idle' | 'running' | 'completed' | 'failed'
  stages: Record<string, StageRecord>
  stageOrder: StageInfo[]
  currentStage: string | null
  error: string | null
  logs: Array<{ line: string; stream?: 'stdout' | 'stderr' }>
}

const INITIAL: BootstrapStateModel = {
  status: 'idle',
  stages: {},
  stageOrder: [],
  currentStage: null,
  error: null,
  logs: []
}

export type Route = 'welcome' | 'progress' | 'success' | 'failure'

export const $route = atom<Route>('welcome')
export const $bootstrap = atom<BootstrapStateModel>(INITIAL)
export const $logPath = atom<string | null>(null)

export function stageTitle(stages: StageInfo[], name: string | null): string {
  if (!name) return '准备中…'
  return stages.find((s) => s.name === name)?.title ?? '准备中…'
}

export const $progress = computed($bootstrap, (b) => {
  const done = b.stageOrder.filter((s) => {
    const state = b.stages[s.name]?.state
    return state === 'succeeded' || state === 'skipped'
  }).length
  return { done, total: b.stageOrder.length }
})

type BootstrapEvent =
  | { type: 'manifest'; stages: StageInfo[] }
  | { type: 'stage'; name: string; state: StageState; durationMs?: number; error?: string }
  | { type: 'log'; line: string; stream?: 'stdout' | 'stderr' }
  | { type: 'complete' }
  | { type: 'failed'; error: string }

let routeTimer: ReturnType<typeof setTimeout> | null = null
let installRevision = 0
let startRequest: Promise<void> | null = null

function clearRouteTimer(): void {
  if (routeTimer != null) {
    clearTimeout(routeTimer)
    routeTimer = null
  }
}

function handleBootstrapEvent(payload: BootstrapEvent): void {
  const cur = $bootstrap.get()
  switch (payload.type) {
    case 'manifest':
      clearRouteTimer()
      $bootstrap.set({
        ...cur,
        status: 'running',
        stages: Object.fromEntries(payload.stages.map((s) => [s.name, { state: null }])),
        stageOrder: payload.stages,
        currentStage: null,
        error: null,
        logs: []
      })
      $route.set('progress')
      break
    case 'stage': {
      if (!cur.stages[payload.name]) {
        console.warn('stage event for unknown stage', payload.name)
        break
      }
      $bootstrap.set({
        ...cur,
        stages: { ...cur.stages, [payload.name]: { state: payload.state, durationMs: payload.durationMs } },
        currentStage: payload.state === 'running' ? payload.name : cur.currentStage
      })
      break
    }
    case 'log': {
      // 限制滚动缓冲，避免长流程日志导致前端 OOM。
      const logs = [...cur.logs, { line: payload.line, stream: payload.stream }]
      $bootstrap.set({ ...cur, logs: logs.length > 2000 ? logs.slice(-2000) : logs })
      break
    }
    case 'complete':
      clearRouteTimer()
      $bootstrap.set({ ...cur, status: 'completed', currentStage: null })
      routeTimer = setTimeout(() => {
        routeTimer = null
        $route.set('success')
      }, 2200)
      break
    case 'failed':
      clearRouteTimer()
      $bootstrap.set({ ...cur, status: 'failed', error: payload.error, currentStage: null })
      routeTimer = setTimeout(() => {
        routeTimer = null
        $route.set('failure')
      }, 1500)
      break
  }
}

export async function initialize(): Promise<void> {
  try {
    $logPath.set(await invoke<string>('get_log_path'))
  } catch (err) {
    console.warn('failed to fetch installer log path', err)
  }
}

export async function startInstall(): Promise<void> {
  if ($bootstrap.get().status === 'running') return
  const revision = ++installRevision
  clearRouteTimer()
  $bootstrap.set({ ...INITIAL, status: 'running' })
  $route.set('progress')

  const channel = new Channel<BootstrapEvent>()
  channel.onmessage = (payload) => {
    if (revision === installRevision) handleBootstrapEvent(payload)
  }
  startRequest = invoke<void>('start_bootstrap', { onEvent: channel })
  try {
    await startRequest
  } catch (error) {
    if (revision !== installRevision) return
    clearRouteTimer()
    $bootstrap.set({
      ...$bootstrap.get(),
      status: 'failed',
      currentStage: null,
      error: error instanceof Error ? error.message : String(error)
    })
    $route.set('failure')
  } finally {
    if (revision === installRevision) startRequest = null
  }
}

export async function cancelInstall(): Promise<void> {
  try {
    await startRequest
  } catch {
    // 启动失败已由 startInstall 呈现，不阻止关闭窗口。
    return
  }
  if ($bootstrap.get().status !== 'running') return
  await invoke('cancel_bootstrap')
}

export async function launchSpiritAgentDesktop(): Promise<void> {
  await invoke('launch_spiritagent_desktop')
}

export async function openLogDir(): Promise<void> {
  await invoke('open_log_dir')
}
