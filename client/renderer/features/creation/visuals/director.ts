import { atom } from 'nanostores'

import {
  accountStorageKey,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler
} from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { isRecord } from '@client-shared/is-record'

import {
  $performance,
  bindPerformanceScheduler,
  type PerformancePorts,
  receivePerformanceCommand,
  requestPerformance,
  syncPerformance
} from './performance-scheduler'
import { contextAllowed, leastRecentlyUsed } from './selection'
import { ambientCandidates, type AmbientChoice, mediaKey, sceneForStage, type WakePerformance } from './stage-plan'
import type { VisualsPresentation } from './types'

type CatalogEntry = VisualsPresentation['ambient_catalog'][number]

type InteractionKind = 'look' | 'smile' | 'stretch'

export const $ambientLoopChoice = atom<AmbientChoice | null>(null)
export const $wakePerformance = atom<WakePerformance | null>(null)

const AMBIENT_INTERVAL_MS = 90_000
const SUBTLE_AMBIENT_FACTOR = 2
const INTERACTION_COOLDOWN_MS: Record<InteractionKind, number> = { look: 20_000, smile: 45_000, stretch: 5 * 60_000 }
const INTERACTION_TTL_MS = 8_000
const CLOCK_RETRY_MS = 5_000

let presentation: VisualsPresentation | null = null
let ambientTimer: ReturnType<typeof setTimeout> | null = null
let ambientDueAt: number | null = null
let ambientStartedAt: number | null = null
let clockTimer: ReturnType<typeof setTimeout> | null = null
let clockDueAt: number | null = null
let wakeTimer: ReturnType<typeof setTimeout> | null = null
let stageVisibleSince: number | null = null
let directorBound = false
let mechanicallyBlocked: () => boolean = () => false
let stopBinding: (() => void) | null = null
let stageEligible: () => boolean = () => false
let motionSubtle: () => boolean = () => false

let refreshAtBoundary: () => Promise<void> = async () => {}
export const $failedStageMedia = atom<ReadonlySet<string>>(new Set())

/** 壁纸和自主动作共用版本失败记录，候选与轮换同时切换。 */
export function recordStageMediaFailure(key: string): void {
  const failedVersions = new Set($failedStageMedia.get())

  if (failedVersions.has(key)) {
    return
  }

  failedVersions.add(key)
  const oldest = failedVersions.values().next().value

  if (failedVersions.size > 64 && oldest) {
    failedVersions.delete(oldest)
  }

  $failedStageMedia.set(failedVersions)
  syncAmbientChoice()
  scheduleAmbient()
}

export function retryStageMedia(key: string): void {
  if ($failedStageMedia.get().has(key)) {
    const remaining = new Set($failedStageMedia.get())
    remaining.delete(key)
    $failedStageMedia.set(remaining)
    syncAmbientChoice()
    scheduleAmbient()
  }
}

// 信号只消费实时 storage 事件，账户、舞台代次与事件身份共同隔离迟到和重复投递。
const INTERACTION_SIGNAL_KEY = registerCompanionStorageKey('da.visuals.interactionSignal')
const seenSignals = new Set<string>()
// 常驻轮换的近期使用记录（本地、有限容量）；轮换优先选择久未出现的循环。
const recentLoops = new Map<string, number>()

function rememberLoop(entry: CatalogEntry, at = Date.now()): void {
  const key = `${entry.visual_id}:${entry.version}`
  recentLoops.set(key, at)

  if (recentLoops.size > 64) {
    const oldest = [...recentLoops.entries()].sort(([, a], [, b]) => a - b)[0]

    if (oldest) {
      recentLoops.delete(oldest[0])
    }
  }
}

function consumeSignal(value: unknown): void {
  const auth = $auth.get()

  if (
    !isRecord(value) ||
    auth.kind !== 'authenticated' ||
    value.accountId !== auth.snapshot.accountId ||
    value.stageEpoch !== $presentation.get().stageEpoch ||
    typeof value.eventId !== 'string' ||
    typeof value.at !== 'number' ||
    !Number.isFinite(value.at) ||
    value.at > Date.now() + 5_000 ||
    Date.now() - value.at > INTERACTION_TTL_MS ||
    seenSignals.has(value.eventId) ||
    (value.kind !== 'look' && value.kind !== 'smile' && value.kind !== 'stretch')
  ) {
    return
  }

  seenSignals.add(value.eventId)
  const oldest = seenSignals.values().next().value

  if (seenSignals.size > 128 && oldest) {
    seenSignals.delete(oldest)
  }

  performInteraction(value.kind, value.eventId, value.at)
}

function candidates(): CatalogEntry[] {
  return presentation
    ? ambientCandidates(
        presentation,
        $presentation.get().effectiveMode === 'desktop',
        motionSubtle(),
        $failedStageMedia.get()
      )
    : []
}

function selectAmbient(entry: CatalogEntry | null): void {
  ambientStartedAt = null
  $ambientLoopChoice.set(
    entry ? { id: `render:ambient:${crypto.randomUUID()}`, visualId: entry.visual_id, version: entry.version } : null
  )
}

function syncAmbientChoice(): void {
  if (!stageEligible() || mechanicallyBlocked()) {
    return
  }

  const pool = candidates()
  const choice = $ambientLoopChoice.get()

  if (!resolveAmbientLoop(presentation)) {
    if (pool.length > 0 || choice) {
      selectAmbient(leastRecentlyUsed(pool, recentLoops))
    }
  }
}

function pickReaction(catalog: CatalogEntry[], kind: InteractionKind): CatalogEntry | null {
  const matches = catalog.filter(
    entry =>
      entry.asset_kind === 'actor' &&
      entry.clip_kind === 'once' &&
      entry.performance_role === 'reaction' &&
      entry.preset_key === kind &&
      contextAllowed(entry, presentation?.intent ?? null)
  )

  return matches.length > 0 ? matches[Math.floor(Math.random() * matches.length)] : null
}

function performInteraction(kind: InteractionKind, eventId: string, at: number): void {
  if (
    !stageEligible() ||
    mechanicallyBlocked() ||
    !presentation ||
    presentation.rhythm?.state === 'asleep' ||
    $wakePerformance.get()
  ) {
    return
  }

  const entry = pickReaction(presentation.ambient_catalog, kind)

  if (!entry) {
    return
  }

  requestPerformance({
    performanceId: eventId,
    source: 'interaction',
    visualId: entry.visual_id,
    visualVersion: entry.version,
    interruptible: entry.interruptible,
    intentRevision: null,
    createdAt: at,
    expiresAt: at + INTERACTION_TTL_MS,
    durationMs: entry.duration_ms,
    cooldownKey: kind,
    cooldownMs: INTERACTION_COOLDOWN_MS[kind]
  })
}

function clearWake(): void {
  if (wakeTimer !== null) {
    clearTimeout(wakeTimer)
    wakeTimer = null
  }

  $wakePerformance.set(null)
}

function considerWake(previous: VisualsPresentation | null, next: VisualsPresentation | null): void {
  const wake = next?.rhythm?.wake
  const plan = next?.stage_plan
  const clip = plan?.wake_actor

  if (
    !wake ||
    !plan ||
    !clip ||
    previous?.rhythm?.state !== 'asleep' ||
    next.rhythm?.state !== 'awake' ||
    stageVisibleSince === null ||
    Date.parse(wake.occurred_at) < stageVisibleSince ||
    Date.parse(wake.expires_at) <= Date.now() ||
    !stageEligible() ||
    mechanicallyBlocked() ||
    $performance.get().state !== 'idle' ||
    next.pending_play ||
    (wake.reason === 'morning' && !next.autonomous_enabled)
  ) {
    return
  }

  clearWake()
  $wakePerformance.set({
    id: `render:wake:${wake.id}`,
    expiresAt: wake.expires_at,
    actor: clip,
    scene: sceneForStage(plan.wake_scene_media, plan.wake_scene, plan.day_phase)
  })
  wakeTimer = setTimeout(clearWake, Math.max(0, Date.parse(wake.expires_at) - Date.now()))
}

export function recordLocalStagePlayback(id: string, status: 'started' | 'finished' | 'failed' | 'interrupted'): void {
  const wake = $wakePerformance.get()

  if (wake?.id === id) {
    if (status === 'started') {
      if (wakeTimer !== null) {
        clearTimeout(wakeTimer)
      }

      wakeTimer = setTimeout(clearWake, (wake.actor.duration_ms ?? 15_000) + 5_000)
    } else {
      clearWake()
      scheduleAmbient()
    }

    return
  }

  const choice = $ambientLoopChoice.get()

  if (choice?.id !== id) {
    return
  }

  const entry = resolveAmbientLoop(presentation)

  if (entry && status === 'started' && ambientStartedAt === null) {
    ambientStartedAt = Date.now()
    rememberLoop(entry, ambientStartedAt)
  } else if (entry && status === 'failed') {
    recordStageMediaFailure(mediaKey(entry))

    return
  }

  scheduleAmbient()
}

function scheduleAmbient(): void {
  const available = candidates()
  const current = resolveAmbientLoop(presentation)
  const started = ambientStartedAt

  const due =
    directorBound &&
    stageEligible() &&
    !mechanicallyBlocked() &&
    !$wakePerformance.get() &&
    $performance.get().state === 'idle' &&
    available.length > 1 &&
    current &&
    started !== null
      ? started + Math.max(AMBIENT_INTERVAL_MS, current.duration_ms ?? 0) * (motionSubtle() ? SUBTLE_AMBIENT_FACTOR : 1)
      : null

  if (due === ambientDueAt) {
    return
  }

  if (ambientTimer !== null) {
    clearTimeout(ambientTimer)
  }

  ambientDueAt = due
  ambientTimer =
    due === null
      ? null
      : setTimeout(
          () => {
            ambientTimer = null
            ambientDueAt = null
            selectAmbient(
              leastRecentlyUsed(
                candidates().filter(entry => mediaKey(entry) !== mediaKey(current!)),
                recentLoops
              )
            )
          },
          Math.max(0, due - Date.now())
        )
}

function scheduleClock(): void {
  const dates = [presentation?.rhythm?.next_change_at, presentation?.stage_plan?.next_phase_at]
  const retryAt = presentation?.static_presentation?.retry_at

  if (retryAt && Date.parse(retryAt) > Date.now()) {
    dates.push(retryAt)
  }

  const times = dates.flatMap(date => {
    const instant = date ? Date.parse(date) : NaN

    return Number.isFinite(instant) ? [instant] : []
  })

  const due = times.length > 0 ? Math.min(...times) : null

  if (!directorBound || due === clockDueAt) {
    return
  }

  if (clockTimer !== null) {
    clearTimeout(clockTimer)
  }

  clockDueAt = due

  if (due !== null) {
    clockTimer = setTimeout(() => void runClock(due), Math.max(0, due - Date.now()))
  } else {
    clockTimer = null
  }
}

async function runClock(due: number): Promise<void> {
  clockTimer = null
  const refresh = refreshAtBoundary
  await refresh()

  if (directorBound && refresh === refreshAtBoundary && due === clockDueAt && clockTimer === null) {
    clockTimer = setTimeout(() => void runClock(due), CLOCK_RETRY_MS)
  }
}

/** 单击、进入对话和聊天反应共用入口；网关反应用其事件身份跨窗口去重。 */
export function enqueueInteraction(
  kind: InteractionKind,
  eventId: string = crypto.randomUUID(),
  at = Date.now()
): void {
  if (!directorBound) {
    enqueueInteractionForward(kind, eventId, at)

    return
  }

  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return
  }

  consumeSignal({ kind, eventId, at, accountId: auth.snapshot.accountId, stageEpoch: $presentation.get().stageEpoch })
}

export function enqueueInteractionForward(
  kind: InteractionKind,
  eventId: string = crypto.randomUUID(),
  at = Date.now()
): void {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return
  }

  persistString(
    INTERACTION_SIGNAL_KEY,
    JSON.stringify({
      kind,
      eventId,
      at,
      accountId: auth.snapshot.accountId,
      stageEpoch: $presentation.get().stageEpoch
    })
  )
}

/** 业务模块只选媒体，资格、机械交互与播放管线由宿主装配。 */
export function bindPresentationDirector(
  ports: Omit<PerformancePorts, 'valid'>,
  presentationFeed: () => VisualsPresentation | null,
  eligibility: {
    isEligible: () => boolean
    isSubtle: () => boolean
    isMechanical: () => boolean
    onChanged: (notify: () => void) => () => void
  }
): () => void {
  stopBinding?.()
  presentation = presentationFeed()
  stageEligible = eligibility.isEligible
  motionSubtle = eligibility.isSubtle
  mechanicallyBlocked = eligibility.isMechanical
  refreshAtBoundary = ports.restore
  directorBound = true

  const stopScheduler = bindPerformanceScheduler({
    ...ports,
    valid: request =>
      request.source === 'manual' ||
      Boolean(
        presentation?.ambient_catalog.some(
          entry =>
            entry.visual_id === request.visualId &&
            entry.version === request.visualVersion &&
            presentation?.rhythm?.state !== 'asleep' &&
            contextAllowed(entry, presentation?.intent ?? null)
        )
      )
  })

  const initialPendingId = presentation?.pending_play?.play_id

  const refresh = (): void => {
    const eligible = stageEligible() && !mechanicallyBlocked()
    stageVisibleSince = eligible ? (stageVisibleSince ?? Date.now()) : null

    if (!eligible) {
      clearWake()
    }

    syncPerformance(stageEligible(), mechanicallyBlocked())

    if (eligible && presentation?.pending_play) {
      receivePerformanceCommand(presentation.pending_play, presentation.pending_play.play_id === initialPendingId)
    }

    syncAmbientChoice()
    scheduleAmbient()
    scheduleClock()
  }

  const onStorage = (event: StorageEvent): void => {
    if (event.key !== accountStorageKey(INTERACTION_SIGNAL_KEY) || !event.newValue) {
      return
    }

    let value: unknown

    try {
      value = JSON.parse(event.newValue)
    } catch {
      return
    }

    consumeSignal(value)
  }

  const onFocus = (): void => {
    if (clockDueAt !== null && clockDueAt <= Date.now()) {
      void refreshAtBoundary()
    }
  }

  window.addEventListener('storage', onStorage)
  window.addEventListener('focus', onFocus)
  const stopEligibility = eligibility.onChanged(refresh)

  const stopPerformance = $performance.listen(performance => {
    if (performance.state !== 'idle') {
      clearWake()
    }

    scheduleAmbient()
  })

  refresh()
  let stopped = false

  const stop = (): void => {
    if (stopped) {
      return
    }

    stopped = true
    stopEligibility()
    stopPerformance()
    window.removeEventListener('storage', onStorage)
    window.removeEventListener('focus', onFocus)
    directorBound = false
    stopScheduler()

    if (ambientTimer !== null) {
      clearTimeout(ambientTimer)
    }

    ambientTimer = null
    ambientDueAt = null
    ambientStartedAt = null

    if (clockTimer !== null) {
      clearTimeout(clockTimer)
    }

    clockTimer = null
    clockDueAt = null
    clearWake()
    stageVisibleSince = null
    presentation = null
    seenSignals.clear()
    recentLoops.clear()
    $failedStageMedia.set(new Set())
    $ambientLoopChoice.set(null)

    if (stopBinding === stop) {
      stopBinding = null
    }
  }

  stopBinding = stop

  return stop
}

/** 网关和回源经过同一入口；一次性起身只消费可见期间发生的睡眠到清醒转换。 */
export function feedDirectorPresentation(next: VisualsPresentation | null): void {
  const previous = presentation
  presentation = next

  if (!directorBound) {
    return
  }

  syncPerformance(stageEligible() && next !== null, mechanicallyBlocked())

  if (next?.pending_play) {
    receivePerformanceCommand(next.pending_play, previous === null)
  }

  if (!next) {
    selectAmbient(null)
    clearWake()
  } else {
    if (next.rhythm?.state === 'asleep') {
      clearWake()
    }

    considerWake(previous, next)
    syncAmbientChoice()
  }

  scheduleAmbient()
  scheduleClock()
}

function resolveAmbientLoop(next: VisualsPresentation | null): CatalogEntry | null {
  const choice = $ambientLoopChoice.get()

  return choice && next
    ? (candidates().find(entry => entry.visual_id === choice.visualId && entry.version === choice.version) ?? null)
    : null
}

registerStorageClearHandler(() => {
  stopBinding?.()
  stageEligible = () => false
  motionSubtle = () => false
  mechanicallyBlocked = () => false
})
