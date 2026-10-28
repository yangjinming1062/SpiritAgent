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
import type { VisualsPresentation } from './types'

type CatalogEntry = VisualsPresentation['ambient_catalog'][number]

export type InteractionKind = 'look' | 'smile' | 'stretch'

/** 当前常驻循环选择（本地、只读目录内轮换）；null 表示沿用服务端 ambient/composition 投影。 */
export const $ambientLoopChoice = atom<{ visualId: string; version: number } | null>(null)

const AMBIENT_INTERVAL_MS = 90_000
const AMBIENT_JITTER_MS = 45_000
const INTERACTION_COOLDOWN_MS: Record<InteractionKind, number> = { look: 20_000, smile: 45_000, stretch: 5 * 60_000 }
const INTERACTION_TTL_MS = 8_000

let presentation: VisualsPresentation | null = null
let ambientTimer: ReturnType<typeof setTimeout> | null = null
let attemptedIntentRevision = -1
let directorBound = false
let mechanicallyBlocked: () => boolean = () => false
let stopBinding: (() => void) | null = null
// 装配方注入的舞台资格判断（登录态 + 动态表现偏好）；模块自身不依赖 character。
let stageEligible: () => boolean = () => false
let motionSubtle: () => boolean = () => false

// 信号只消费实时 storage 事件，账户、舞台代次与事件身份共同隔离迟到和重复投递。
const INTERACTION_SIGNAL_KEY = registerCompanionStorageKey('da.visuals.interactionSignal')
const seenSignals = new Set<string>()

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

function loopEntries(catalog: CatalogEntry[]): CatalogEntry[] {
  return catalog.filter(entry => entry.asset_kind === 'actor' && entry.clip_kind !== 'once')
}

function syncAmbientChoice(catalog: CatalogEntry[]): void {
  const loops = loopEntries(catalog)
  const choice = $ambientLoopChoice.get()

  if (loops.length === 0) {
    $ambientLoopChoice.set(null)

    return
  }

  if (!choice || !loops.some(entry => entry.visual_id === choice.visualId && entry.version === choice.version)) {
    const idle = loops.find(entry => entry.preset_key === 'idle') ?? loops[0]
    $ambientLoopChoice.set({ visualId: idle.visual_id, version: idle.version })
  }
}

function onceEntries(catalog: CatalogEntry[]): CatalogEntry[] {
  return catalog.filter(entry => entry.asset_kind === 'actor' && entry.clip_kind === 'once')
}

function pickAmbient(catalog: CatalogEntry[]): CatalogEntry | null {
  const loops = loopEntries(catalog)

  const intent = presentation?.intent

  const context = intent
    ? [intent.activity, intent.scene_title, intent.mood, intent.focus, intent.activity_kind, ...intent.visual_tags]
        .join(' ')
        .toLocaleLowerCase()
    : ''

  const matches = (condition: string): boolean => {
    const tokens = intentKeywords(condition.toLocaleLowerCase())

    return tokens.length > 0 && tokens.some(token => context.includes(token))
  }

  const compatible = intent ? loops.filter(entry => !entry.avoid_when.some(matches)) : loops
  const candidatesPool = compatible.length > 0 ? compatible : loops

  if (candidatesPool.length <= 1) {
    return candidatesPool[0] ?? null
  }

  const current = $ambientLoopChoice.get()
  const candidates = candidatesPool.filter(entry => entry.visual_id !== current?.visualId)
  const subtle = motionSubtle()

  const weights = candidates.map(entry => {
    const levelWeight = subtle && entry.preset_key !== 'idle' ? 0.5 : 1
    const contextWeight = intent && entry.use_when.some(matches) ? 2 : 1

    return levelWeight * contextWeight
  })

  const total = weights.reduce((sum, weight) => sum + weight, 0)
  let cursor = Math.random() * total

  for (let index = 0; index < candidates.length; index += 1) {
    cursor -= weights[index]

    if (cursor <= 0) {
      return candidates[index]
    }
  }

  return candidates[candidates.length - 1] ?? null
}

function pickReaction(catalog: CatalogEntry[], kind: InteractionKind): CatalogEntry | null {
  const matches = onceEntries(catalog).filter(entry => entry.preset_key === kind)

  if (matches.length > 0) {
    return matches[Math.floor(Math.random() * matches.length)]
  }

  return null
}

function performInteraction(kind: InteractionKind, eventId: string, at: number): void {
  if (!stageEligible() || mechanicallyBlocked() || !presentation) {
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
    interruptible: false,
    intentRevision: null,
    createdAt: at,
    expiresAt: at + INTERACTION_TTL_MS,
    durationMs: entry.duration_ms,
    cooldownKey: kind,
    cooldownMs: INTERACTION_COOLDOWN_MS[kind]
  })
}

/** 分词重叠评分：活动常是完整句子，与画面描述按关键词重叠计分而非整句包含。 */
function overlapScore(text: string, keywords: string[]): number {
  if (!text || keywords.length === 0) {
    return 0
  }

  return keywords.reduce((score, word) => (word && text.includes(word) ? score + 1 : score), 0)
}

function intentKeywords(activity: string): string[] {
  return activity
    .split(/[\s，。、；：？！,.;:?!'"“”‘’（）()[\]【】《》]+/)
    .map(word => word.trim())
    .filter(word => word.length >= 2)
    .slice(0, 12)
}

function evaluateIntent(): void {
  const intent = presentation?.intent

  if (!intent || intent.life_revision === attemptedIntentRevision || intent.activity_kind === 'intent') {
    return
  }

  const catalog = presentation?.ambient_catalog ?? []
  const keywords = intentKeywords(intent.activity.trim())
  const sceneTitle = intent.scene_title.trim()
  let best: CatalogEntry | null = null
  let bestScore = 0

  for (const entry of catalog) {
    if (entry.asset_kind !== 'cinematic') {
      continue
    }

    // 活动关键词与画面/动作描述的重叠度；场景标题单独计一分。
    const overlap = Math.max(overlapScore(entry.description, keywords), overlapScore(entry.action_name, keywords))
    const sceneMatch = sceneTitle && entry.description.includes(sceneTitle) ? 1 : 0
    const score = Math.min(2, overlap + sceneMatch)

    if (score > bestScore) {
      best = entry
      bestScore = score
    }
  }

  // 至少两个关键词命中（或关键词命中加场景一致）才切持续画面；否则保留当前画面，不把无关视频当成生活。
  if (best && bestScore >= 2) {
    if (
      requestPerformance({
        performanceId: `life:${intent.life_revision}:${best.visual_id}:${best.version}`,
        source: 'life',
        visualId: best.visual_id,
        visualVersion: best.version,
        interruptible: true,
        intentRevision: intent.life_revision,
        createdAt: Date.now(),
        expiresAt: Date.now() + 30_000,
        durationMs: best.duration_ms
      })
    ) {
      attemptedIntentRevision = intent.life_revision
    }
  }
}

function scheduleAmbient(): void {
  if (ambientTimer !== null) {
    clearTimeout(ambientTimer)
    ambientTimer = null
  }

  if (
    !stageEligible() ||
    !directorBound ||
    !presentation ||
    mechanicallyBlocked() ||
    $performance.get().state !== 'idle' ||
    loopEntries(presentation.ambient_catalog).length === 0
  ) {
    return
  }

  const current = resolveAmbientLoop(presentation)
  const duration = current?.duration_ms ?? AMBIENT_INTERVAL_MS
  // 轮换间隔与当前循环时长联动（一个循环的 1–3 倍，下限 90s），避免长视频播一半就被切换。
  const interval = Math.max(AMBIENT_INTERVAL_MS, Math.min(3 * AMBIENT_INTERVAL_MS, duration))
  ambientTimer = setTimeout(
    () => {
      ambientTimer = null
      rotateAmbient()
    },
    interval + Math.random() * AMBIENT_JITTER_MS
  )
}

function rotateAmbient(): void {
  if (!stageEligible() || !presentation || mechanicallyBlocked() || $performance.get().state !== 'idle') {
    return
  }

  const pick = pickAmbient(presentation.ambient_catalog)

  if (pick) {
    $ambientLoopChoice.set({ visualId: pick.visual_id, version: pick.version })
  }

  scheduleAmbient()
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
  attemptedIntentRevision = presentation?.intent?.life_revision ?? -1
  stageEligible = eligibility.isEligible
  motionSubtle = eligibility.isSubtle
  mechanicallyBlocked = eligibility.isMechanical
  directorBound = true

  const stopScheduler = bindPerformanceScheduler({
    ...ports,
    valid: request =>
      request.source === 'manual' ||
      Boolean(
        presentation &&
        (request.intentRevision === null || request.intentRevision === presentation.intent?.life_revision) &&
        presentation.ambient_catalog.some(
          entry => entry.visual_id === request.visualId && entry.version === request.visualVersion
        )
      )
  })

  const initialPendingId = presentation?.pending_play?.play_id

  const refresh = (): void => {
    syncPerformance(stageEligible(), mechanicallyBlocked())

    if (stageEligible()) {
      if (presentation?.pending_play) {
        receivePerformanceCommand(presentation.pending_play, presentation.pending_play.play_id === initialPendingId)
      }

      syncAmbientChoice(presentation?.ambient_catalog ?? [])
      evaluateIntent()
    } else {
      $ambientLoopChoice.set(null)
    }

    scheduleAmbient()
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

  window.addEventListener('storage', onStorage)
  const stopEligibility = eligibility.onChanged(refresh)
  const stopPerformance = $performance.listen(() => scheduleAmbient())
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
    directorBound = false
    stopScheduler()

    if (ambientTimer !== null) {
      clearTimeout(ambientTimer)
    }

    ambientTimer = null
    attemptedIntentRevision = -1
    presentation = null
    seenSignals.clear()
    $ambientLoopChoice.set(null)

    if (stopBinding === stop) {
      stopBinding = null
    }
  }

  stopBinding = stop

  return stop
}

/** 呈现变化使旧生活任务失效；回执刷新不会再次提交同一修订。 */
export function feedDirectorPresentation(next: VisualsPresentation | null): void {
  const hydrated = presentation !== null
  presentation = next

  if (!directorBound) {
    return
  }

  syncPerformance(stageEligible() && next !== null, mechanicallyBlocked())

  if (next?.pending_play) {
    receivePerformanceCommand(next.pending_play, !hydrated)
  }

  if (!next) {
    attemptedIntentRevision = -1
    $ambientLoopChoice.set(null)
  } else if (stageEligible()) {
    if (!hydrated) {
      attemptedIntentRevision = next.intent?.life_revision ?? -1
    }

    syncAmbientChoice(next.ambient_catalog)
    evaluateIntent()
  }

  scheduleAmbient()
}

/** 常驻循环的当前本地选择解析为目录条目；无选择或目录缺项时返回 null（沿用服务端投影）。 */
export function resolveAmbientLoop(next: VisualsPresentation | null): CatalogEntry | null {
  const choice = $ambientLoopChoice.get()

  if (!choice || !next) {
    return null
  }

  return (
    next.ambient_catalog.find(entry => entry.visual_id === choice.visualId && entry.version === choice.version) ?? null
  )
}

registerStorageClearHandler(() => {
  stopBinding?.()
  stageEligible = () => false
  motionSubtle = () => false
  mechanicallyBlocked = () => false
})
