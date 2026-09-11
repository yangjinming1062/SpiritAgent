import { atom, computed } from 'nanostores'
import { Channel, invoke } from '@tauri-apps/api/core'

// bootstrap 状态的单一数据源；按 payload.type 处理 Channel 传递的事件。

export interface StageInfo {
  name: string
  title: string
  category: string
  needs_user_input: boolean
}

export type StageState = 'running' | 'succeeded' | 'skipped' | 'failed'

export interface StageRecord {
  info: StageInfo
  state: StageState | null
  durationMs?: number
  error?: string
}

export interface BootstrapStateModel {
  status: 'idle' | 'running' | 'completed' | 'failed'
  protocolVersion: number | null
  stages: Record<string, StageRecord>
  stageOrder: string[]
  currentStage: string | null
  installRoot: string | null
  error: string | null
  logs: Array<{ stage?: string; line: string; stream?: 'stdout' | 'stderr' }>
}

const INITIAL: BootstrapStateModel = {
  status: 'idle',
  protocolVersion: null,
  stages: {},
  stageOrder: [],
  currentStage: null,
  installRoot: null,
  error: null,
  logs: []
}

export type Route = 'welcome' | 'progress' | 'success' | 'failure'

export const $route = atom<Route>('welcome')
export const $bootstrap = atom<BootstrapStateModel>(INITIAL)
export const $logPath = atom<string | null>(null)
export const $spiritAgentHome = atom<string | null>(null)

export const $progress = computed($bootstrap, (b) => {
  const total = b.stageOrder.length
  if (total === 0) return { done: 0, total: 0, fraction: 0 }
  let done = 0
  for (const name of b.stageOrder) {
    const s = b.stages[name]?.state
    if (s === 'succeeded' || s === 'skipped' || s === 'failed') done += 1
  }
  return { done, total, fraction: done / total }
})

interface BootstrapManifestEvent {
  type: 'manifest'
  stages: StageInfo[]
  protocolVersion: number | null
}

interface BootstrapStageEvent {
  type: 'stage'
  name: string
  state: StageState
  durationMs?: number
  result?: {
    stage: string
    ok: boolean
    skipped: boolean
    reason?: string
  }
  error?: string
}

interface BootstrapLogEvent {
  type: 'log'
  stage?: string
  line: string
  stream?: 'stdout' | 'stderr'
}

interface BootstrapCompleteEvent {
  type: 'complete'
  installRoot: string
  marker: unknown
}

interface BootstrapFailedEvent {
  type: 'failed'
  stage?: string
  error: string
}

type BootstrapEvent =
  | BootstrapManifestEvent
  | BootstrapStageEvent
  | BootstrapLogEvent
  | BootstrapCompleteEvent
  | BootstrapFailedEvent

let routeTimer: ReturnType<typeof setTimeout> | null = null

function clearRouteTimer(): void {
  if (routeTimer != null) {
    clearTimeout(routeTimer)
    routeTimer = null
  }
}

function handleBootstrapEvent(payload: BootstrapEvent): void {
  const cur = $bootstrap.get()
  switch (payload.type) {
    case 'manifest': {
      clearRouteTimer()
      const stages: Record<string, StageRecord> = {}
      const order: string[] = []
      for (const s of payload.stages) {
        stages[s.name] = { info: s, state: null }
        order.push(s.name)
      }
      $bootstrap.set({
        ...cur,
        status: 'running',
        protocolVersion: payload.protocolVersion,
        stages,
        stageOrder: order,
        currentStage: null,
        installRoot: null,
        error: null,
        logs: []
      })
      $route.set('progress')
      break
    }
    case 'stage': {
      const existing = cur.stages[payload.name]
      if (!existing) {
        console.warn('stage event for unknown stage', payload.name)
        break
      }
      const next: StageRecord = {
        ...existing,
        state: payload.state,
        durationMs: payload.durationMs,
        error: payload.error
      }
      $bootstrap.set({
        ...cur,
        stages: { ...cur.stages, [payload.name]: next },
        currentStage:
          payload.state === 'running' ? payload.name : cur.currentStage
      })
      break
    }
    case 'log': {
      const logs = [...cur.logs, { stage: payload.stage, line: payload.line, stream: payload.stream }]
      // 长流程日志可上万行，限制滚动缓冲以避免前端 OOM。
      const trimmed = logs.length > 2000 ? logs.slice(-2000) : logs
      $bootstrap.set({ ...cur, logs: trimmed })
      break
    }
    case 'complete':
      clearRouteTimer()
      $bootstrap.set({
        ...cur,
        status: 'completed',
        installRoot: payload.installRoot,
        currentStage: null
      })
      routeTimer = setTimeout(() => {
        routeTimer = null
        $route.set('success')
      }, 2200)
      break
    case 'failed':
      clearRouteTimer()
      $bootstrap.set({
        ...cur,
        status: 'failed',
        error: payload.error,
        currentStage: null
      })
      routeTimer = setTimeout(() => {
        routeTimer = null
        $route.set('failure')
      }, 1500)
      break
  }
}

export async function initialize(): Promise<void> {
  const cleanup = () => {
    clearRouteTimer()
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', cleanup)
  }

  // 启动时拉取诊断信息（日志路径、SPIRITAGENT_HOME）。
  try {
    const [logPath, spiritAgentHome] = await Promise.all([
      invoke<string>('get_log_path'),
      invoke<string>('get_spiritagent_home')
    ])
    $logPath.set(logPath)
    $spiritAgentHome.set(spiritAgentHome)
  } catch (err) {
    console.warn('failed to fetch installer paths', err)
  }
}

export async function startInstall(): Promise<void> {
  clearRouteTimer()
  // 重试前重置状态；安装脚本在构建期 pin 完毕，commit/branch 始终为 null。
  $bootstrap.set(INITIAL)
  $route.set('progress')

  const channel = new Channel<BootstrapEvent>()
  channel.onmessage = (payload) => {
    handleBootstrapEvent(payload)
  }

  await invoke('start_bootstrap', {
    args: {
      commit: null,
      branch: null,
      include_desktop: true,
      spiritagent_home: null
    },
    onEvent: channel
  })
}

export async function cancelInstall(): Promise<void> {
  clearRouteTimer()
  await invoke('cancel_bootstrap')
}

export async function launchSpiritAgentDesktop(): Promise<void> {
  if (!$bootstrap.get().installRoot) throw new Error('no install root')
  // 桌面端路径由 Rust 侧从 $SPIRITAGENT_HOME 解析（见 src-tauri/src/bootstrap.rs）。
  await invoke('launch_spiritagent_desktop')
}

export async function openLogDir(): Promise<void> {
  await invoke('open_log_dir')
}
