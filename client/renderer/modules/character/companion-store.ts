import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import {
  definePersistedEnum,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'

// 渲染层按 unauthed → onboarding（向导进行中）→ ready（向导完成后）流转。
export type CompanionLifecycle = 'unauthed' | 'onboarding' | 'ready'

// 表现状态机（DESIGN「状态与播放优先级」）。
export type SpriteStateName =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'emotional'
  | 'interacting'
  | 'disconnected'

const lifecyclePersisted = definePersistedEnum<CompanionLifecycle>({
  allowed: ['unauthed', 'ready', 'onboarding'] as const,
  fallback: 'unauthed',
  key: 'da.companion.lifecycle'
})

export const $companionLifecycle = lifecyclePersisted.$atom
export const setCompanionLifecycle = lifecyclePersisted.set

export const $spriteState = atom<SpriteStateName>('idle')
const $previousState = atom<SpriteStateName>('idle')

// 跨模块共享的水合去重缓存：同 key 的并发水合只跑一次。
const inFlightHydrations = new Map<string, Promise<unknown>>()

// 打扰档位门控伙伴的主动行为（DESIGN「主动陪伴」）。
// 三档：still（静止，停止一切主动 LLM 调用与分析，仅响应交互）、
// normal（常规，仅文字问候等原地轻互动）、autonomous（自主，开放桌面精灵视觉与空间表达）。
// 用户主动行为永不被门控——只门控主动外发（companion.message）与主动推理发起。
export type DisturbanceTier = 'still' | 'normal' | 'autonomous'

const DISTURBANCE_TIERS = ['still', 'normal', 'autonomous'] as const satisfies readonly DisturbanceTier[]
const DISTURBANCE_TIER_KEY = 'da.companion.disturbanceTier'

const userPreferredTierPersisted = definePersistedEnum<DisturbanceTier>({
  allowed: DISTURBANCE_TIERS,
  fallback: 'normal',
  key: DISTURBANCE_TIER_KEY,
  preserveOnLogout: true
})

export const $userPreferredTier = userPreferredTierPersisted.$atom

export function setDisturbanceTier(tier: DisturbanceTier): void {
  userPreferredTierPersisted.set(tier)

  try {
    window.spiritagent?.prefs?.set({ key: 'companion.disturbance_preference', value: tier })
  } catch (err) {
    log.warn('companion-store', 'Failed to persist disturbance preference', err)
  }
}

// ``null`` 表示「当前无覆盖；生效档位回退到 user_preferred」。
// 只有活动监视器（activity.ts）会写它。
export const $effectiveTierOverride = atom<DisturbanceTier | null>(null)

// 临时安静（DESIGN「主动陪伴」）：截止前生效档位为静止，到期只清除截止时间，不改写档位偏好。
// 截止时间只存本机、不经 prefs 上云；与档位偏好一样登出不清除——只约束本机，且至多持续一个时长。
export const QUIET_MINUTES = 50
const QUIET_DURATION_MS = QUIET_MINUTES * 60_000
// 系统休眠期间计时器可能停走，按墙钟分段复查是否到期。
const QUIET_RECHECK_MS = 60_000
const QUIET_UNTIL_KEY = registerCompanionStorageKey('da.companion.quietUntil', { preserveOnLogout: true })

// 截止时间（epoch 毫秒）；null 表示不在临时安静中。
export const $quietUntil = atom<number | null>(null)

let quietTimer: ReturnType<typeof setTimeout> | null = null

// 系统时钟回拨时，读出的截止时间也不超过一个时长。
function readStoredQuietUntil(): number | null {
  const stored = storedString(QUIET_UNTIL_KEY)
  const until = stored === null ? Number.NaN : Number(stored)

  return Number.isFinite(until) ? Math.min(until, Date.now() + QUIET_DURATION_MS) : null
}

// 更新本窗状态并重排到期检查；已过期的截止时间直接清除，生效档位随之回到当前偏好与情境。
function applyQuietUntil(until: number | null): void {
  if (quietTimer) {
    clearTimeout(quietTimer)
    quietTimer = null
  }

  if (until !== null && until <= Date.now()) {
    persistString(QUIET_UNTIL_KEY, null)
    $quietUntil.set(null)

    return
  }

  $quietUntil.set(until)

  if (until !== null) {
    quietTimer = setTimeout(() => applyQuietUntil($quietUntil.get()), Math.min(until - Date.now(), QUIET_RECHECK_MS))
  }
}

function setQuietUntil(until: number | null): void {
  persistString(QUIET_UNTIL_KEY, until === null ? null : String(until))
  applyQuietUntil(until)
}

export function startQuiet(): void {
  setQuietUntil(Date.now() + QUIET_DURATION_MS)
}

// 用户手动结束或明确选择档位时取消临时安静。
export function endQuiet(): void {
  setQuietUntil(null)
}

// 其他窗口写入档位偏好或临时安静后，经 storage 事件同步到本窗；key 为 null 表示存储被整体清空。
export function syncDisturbanceFromStorage(key: string | null): void {
  if (key === DISTURBANCE_TIER_KEY || key === null) {
    const stored = storedString(DISTURBANCE_TIER_KEY)
    $userPreferredTier.set(DISTURBANCE_TIERS.find(tier => tier === stored) ?? 'normal')
  }

  if (key === QUIET_UNTIL_KEY || key === null) {
    applyQuietUntil(readStoredQuietUntil())
  }
}

// 加载时按保存的截止时间恢复临时安静，重启不中断。
applyQuietUntil(readStoredQuietUntil())

// 手动静止与临时安静都是硬锁定：即便活动监视器写入 override，生效档位也保持静止；
// 覆盖只在两者都不成立时生效。
export const $effectiveTier = computed(
  [$userPreferredTier, $effectiveTierOverride, $quietUntil],
  (preferred, override, quietUntil) =>
    preferred === 'still' || quietUntil !== null ? 'still' : (override ?? preferred)
)

const STATE_PRIORITY: Record<SpriteStateName, number> = {
  disconnected: 100,
  emotional: 35,
  idle: 10,
  interacting: 80,
  listening: 40,
  speaking: 60,
  thinking: 50,
  working: 70
}

// 瞬态经 ``$previousState`` 与下方计时器自动恢复，因此绕过优先级门控，
// 避免进行中的 WORKING/SPEAKING 压制瞬时的情绪/互动提示。
const TRANSIENT_STATES: ReadonlySet<SpriteStateName> = new Set(['emotional', 'interacting'])

let transientTimer: ReturnType<typeof setTimeout> | null = null
let activityCounter = 0
let activityResetTimer: ReturnType<typeof setTimeout> | null = null

export function setSpriteState(name: SpriteStateName, options?: { durationMs?: number; force?: boolean }): void {
  const current = $spriteState.get()

  if (
    !options?.force &&
    STATE_PRIORITY[name] < STATE_PRIORITY[current] &&
    current !== 'idle' &&
    !TRANSIENT_STATES.has(name)
  ) {
    // 低优先级状态无法打断高优先级状态——瞬时状态除外，
    // 它们会通过下方计时器自动恢复。
    return
  }

  if (TRANSIENT_STATES.has(name)) {
    if (!TRANSIENT_STATES.has(current)) {
      $previousState.set(current)
    }

    $spriteState.set(name)

    if (transientTimer) {
      clearTimeout(transientTimer)
    }

    transientTimer = setTimeout(() => {
      transientTimer = null
      restoreAfterTransient()
    }, options?.durationMs ?? 1800)

    return
  }

  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  $spriteState.set(name)
}

// 若瞬时过程中有更高优先级状态到达，优先取当前状态。
function restoreAfterTransient(): void {
  const currentAfter = $spriteState.get()
  const storedPrev = $previousState.get()

  const target = !TRANSIENT_STATES.has(currentAfter)
    ? currentAfter
    : TRANSIENT_STATES.has(storedPrev)
      ? 'idle'
      : storedPrev

  $spriteState.set(target)
}

// 提前结束仍在进行的指定瞬态（如表达片段收尾），恢复仍有效的持续状态。
export function endTransientState(name: SpriteStateName): void {
  if ($spriteState.get() !== name || !transientTimer) {
    return
  }

  clearTimeout(transientTimer)
  transientTimer = null
  restoreAfterTransient()
}

// 拖拽期间持续保持 interacting：撤销在途瞬态计时器并以按下前的持续状态为恢复目标，
// 松手时由带时长的 setSpriteState('interacting') 负责恢复。
export function holdInteracting(): void {
  const current = $spriteState.get()

  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  if (!TRANSIENT_STATES.has(current)) {
    $previousState.set(current)
  }

  $spriteState.set('interacting')
}

export function reportUserActivity(): void {
  const current = $spriteState.get()

  if (current !== 'idle' && current !== 'working') {
    return
  }

  activityCounter += 1

  if (activityCounter >= 6 && current === 'idle') {
    setSpriteState('working')
  }

  if (activityResetTimer) {
    clearTimeout(activityResetTimer)
  }

  activityResetTimer = setTimeout(() => {
    activityCounter = 0

    if ($spriteState.get() === 'working') {
      // ``working``（优先级 70）盖住 ``idle``（优先级 10）——不带 ``force: true`` 时
      // 计时器到期，但状态仍会卡在 working。显式强制退出，
      // 这样在用户停止活动达到配置窗口后精灵能回到 idle。
      setSpriteState('idle', { force: true })
    }
  }, 10000)
}

// 生效档位（含活动覆盖与临时安静）经配置管道上云，是后端闸门（主动消息 / cron / 视觉与空间推理）
// 的唯一档位来源；与用户偏好分键——生效值是设备派生的，不回写本地偏好。
// 只由精灵窗（活动监视与重连补报）推送：其他窗口没有活动覆盖，推送值可能与实际生效档位不一致。
export function pushEffectiveDisturbanceTier(tier: DisturbanceTier): void {
  window.spiritagent?.prefs?.set({ key: 'companion.disturbance_tier', value: tier })
}

function runOnce(key: string, fn: () => Promise<unknown>): Promise<unknown> {
  let task = inFlightHydrations.get(key)

  if (!task) {
    task = (async () => {
      return await fn()
    })().finally(() => {
      inFlightHydrations.delete(key)
    })
    inFlightHydrations.set(key, task)
  }

  return task
}

export async function ensureCompanionHydrated(deps: {
  hydratePersona: () => Promise<unknown>
  hydratePortrait?: () => Promise<unknown>
}): Promise<void> {
  const tasks = [runOnce('persona', deps.hydratePersona)]

  if (deps.hydratePortrait) {
    tasks.push(runOnce('portrait', deps.hydratePortrait))
  }

  try {
    await Promise.all(tasks)
  } catch (err) {
    log.warn('companion-store', 'ensureCompanionHydrated failed', err)
  }
}

// 清掉所有瞬态/活动计时器与排队状态——登出后 orphan 计时器在新会话里会写 $spriteState。
// 必须在文件末尾：闭包按引用捕获 transientTimer / activityResetTimer / activityCounter / $previousState /
// $effectiveTierOverride，提前声明会在 HMR 同步调用时撞 TDZ。
registerStorageClearHandler(() => {
  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  if (activityResetTimer) {
    clearTimeout(activityResetTimer)
    activityResetTimer = null
  }

  inFlightHydrations.clear()

  activityCounter = 0
  $spriteState.set('idle')
  $previousState.set('idle')
  $effectiveTierOverride.set(null)
})
