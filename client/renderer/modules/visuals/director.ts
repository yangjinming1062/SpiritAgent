import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'
import {
  accountStorageKey,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler
} from '@/shared/lib/storage'

import type { VisualsPresentation } from './types'

type CatalogEntry = VisualsPresentation['ambient_catalog'][number]

export type InteractionKind = 'look' | 'smile' | 'stretch'

/**
 * Presentation Director（客户端实时演出导演）：
 * - 常驻轮换：只读呈现目录，本地选择下一个待机/轻量循环，不建立播放账本；
 * - 互动演出与意图驱动的持续画面变更：经播放端点走后端指令 + 认领 + 回执的既有管线；
 * - 优先级：互动 > 意图 > 常驻；由持续时间、冷却与概率权重控制，不按固定秒强制切换。
 * 同一时刻只有一个舞台宿主绑定导演（窗口模式为精灵窗，桌面模式为桌面窗）；
 * 非宿主窗口的互动请求经 storage 信号转发给宿主消费，避免多窗口并发点播。
 */

/** 当前常驻循环选择（本地、只读目录内轮换）；null 表示沿用服务端 ambient/composition 投影。 */
export const $ambientLoopChoice = atom<{ visualId: string; version: number } | null>(null)

const AMBIENT_INTERVAL_MS = 90_000
const AMBIENT_JITTER_MS = 45_000
const INTERACTION_COOLDOWN_MS: Record<InteractionKind, number> = { look: 20_000, smile: 45_000, stretch: 5 * 60_000 }
const INTERACTION_QUEUE_LIMIT = 2

let presentation: VisualsPresentation | null = null
let ambientTimer: ReturnType<typeof setTimeout> | null = null
let performedIntentRevision = -1
const reactionQueue: InteractionKind[] = []
const cooldownUntil = new Map<InteractionKind, number>()
let performRequest: ((visualId: string) => Promise<void>) | null = null
// 装配方注入的舞台资格判断（登录态 + 动态表现偏好）；模块自身不依赖 character。
let stageEligible: () => boolean = () => false
let motionSubtle: () => boolean = () => false

// 非宿主窗口的互动信号经 storage 转发给宿主窗口（storage 事件跨同源窗口可见）。
// 每次写入都带新时间戳，避免同值写入不触发事件；登出清理时一并清零。
const INTERACTION_SIGNAL_KEY = registerCompanionStorageKey('da.visuals.interactionSignal')
let hostBound = false
let stopSignalListener: (() => void) | null = null

function signalKey(): string {
  return accountStorageKey(INTERACTION_SIGNAL_KEY) ?? INTERACTION_SIGNAL_KEY
}

function startSignalListener(): void {
  if (stopSignalListener !== null) {
    return
  }

  const onStorage = (event: StorageEvent): void => {
    if (event.key !== signalKey() || !event.newValue || !hostBound) {
      return
    }

    try {
      const signal = JSON.parse(event.newValue) as { kind?: unknown }

      if (signal.kind === 'look' || signal.kind === 'smile' || signal.kind === 'stretch') {
        enqueueInteraction(signal.kind)
      }
    } catch {
      // 非宿主写入的未知格式或损坏值忽略。
    }
  }

  window.addEventListener('storage', onStorage)
  stopSignalListener = () => window.removeEventListener('storage', onStorage)
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

function stageReady(): boolean {
  return stageEligible()
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

/** 互动演出走后端指令管线：认领、回执与组合推进由既有机制完成。 */
function perform(visualId: string): void {
  const request = performRequest

  if (!request) {
    return
  }

  request(visualId).catch(error => log.warn('director', 'Performance dispatch failed', error))
}

function drainReactions(now: number): void {
  while (reactionQueue.length > 0) {
    const kind = reactionQueue.shift()!

    if ((cooldownUntil.get(kind) ?? 0) > now) {
      continue
    }

    const entry = presentation ? pickReaction(presentation.ambient_catalog, kind) : null

    if (!entry) {
      continue
    }

    cooldownUntil.set(kind, now + INTERACTION_COOLDOWN_MS[kind])
    perform(entry.visual_id)

    break
  }
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

  if (!intent || intent.life_revision === performedIntentRevision) {
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
    performedIntentRevision = intent.life_revision
    perform(best.visual_id)
  }
}

function scheduleAmbient(): void {
  if (ambientTimer !== null) {
    clearTimeout(ambientTimer)
    ambientTimer = null
  }

  if (!stageReady() || !performRequest || !presentation || loopEntries(presentation.ambient_catalog).length === 0) {
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
  if (!stageReady() || !presentation) {
    return
  }

  const pick = pickAmbient(presentation.ambient_catalog)

  if (pick) {
    $ambientLoopChoice.set({ visualId: pick.visual_id, version: pick.version })
  }

  scheduleAmbient()
}

/** 互动事件入口：精灵单击、聊天反应等；宿主窗口直接入队，非宿主窗口转发给宿主。 */
export function enqueueInteraction(kind: InteractionKind): void {
  if (!hostBound) {
    enqueueInteractionForward(kind)

    return
  }

  if (!stageReady()) {
    return
  }

  if (reactionQueue.length >= INTERACTION_QUEUE_LIMIT) {
    return
  }

  reactionQueue.push(kind)
  drainReactions(Date.now())
}

/** 非宿主窗口的互动信号转发；宿主窗口的 storage 监听消费后入队。 */
export function enqueueInteractionForward(kind: InteractionKind): void {
  persistString(INTERACTION_SIGNAL_KEY, JSON.stringify({ kind, at: Date.now() }))
}

/** 舞台宿主装配导演；资格与偏好判断由装配方注入，导演自身不依赖其他业务模块。 */
export function bindPresentationDirector(
  onPerform: (visualId: string) => Promise<void>,
  presentationFeed: () => VisualsPresentation | null,
  eligibility: { isEligible: () => boolean; isSubtle: () => boolean; onChanged: (notify: () => void) => () => void }
): () => void {
  performRequest = onPerform
  stageEligible = eligibility.isEligible
  motionSubtle = eligibility.isSubtle
  hostBound = true
  startSignalListener()

  const refresh = (): void => {
    if (!stageReady()) {
      if (ambientTimer !== null) {
        clearTimeout(ambientTimer)
        ambientTimer = null
      }

      $ambientLoopChoice.set(null)

      return
    }

    drainReactions(Date.now())
    evaluateIntent()
    scheduleAmbient()
  }

  const stopEligibility = eligibility.onChanged(() => {
    refresh()
    rotateAmbient()
  })

  presentation = presentationFeed()
  syncAmbientChoice(presentation?.ambient_catalog ?? [])
  refresh()

  return () => {
    stopEligibility()

    if (ambientTimer !== null) {
      clearTimeout(ambientTimer)
      ambientTimer = null
    }

    performRequest = null
    presentation = null
    reactionQueue.length = 0
    hostBound = false
    $ambientLoopChoice.set(null)
  }
}

/** 由 visuals-store 在呈现刷新时驱动；导演不自行拉取。 */
export function feedDirectorPresentation(next: VisualsPresentation | null): void {
  presentation = next

  if (!next) {
    if (ambientTimer !== null) {
      clearTimeout(ambientTimer)
      ambientTimer = null
    }

    performedIntentRevision = -1
    reactionQueue.length = 0
    cooldownUntil.clear()
    $ambientLoopChoice.set(null)

    return
  }

  syncAmbientChoice(next.ambient_catalog)

  if (stageReady() && performRequest) {
    drainReactions(Date.now())
    evaluateIntent()
    scheduleAmbient()
  }
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
  if (ambientTimer !== null) {
    clearTimeout(ambientTimer)
    ambientTimer = null
  }

  stopSignalListener?.()
  stopSignalListener = null
  reactionQueue.length = 0
  cooldownUntil.clear()
  performedIntentRevision = -1
  presentation = null
  performRequest = null
  hostBound = false
  stageEligible = () => false
  motionSubtle = () => false
  $ambientLoopChoice.set(null)
})
