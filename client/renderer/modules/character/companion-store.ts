import { DESKTOP_COMPANION_ACTIVITY_PRIORITY, type DesktopCompanionActivityState } from '@ipc/contracts'
import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import {
  definePersistedEnum,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'
import type { SetSpriteStateOptions, SpriteStateName } from '@/shared/presentation-ports'

// 渲染层按 unauthed → onboarding（向导进行中）→ ready（向导完成后）流转。
export type CompanionLifecycle = 'unauthed' | 'onboarding' | 'ready'

const lifecyclePersisted = definePersistedEnum<CompanionLifecycle>({
  allowed: ['unauthed', 'ready', 'onboarding'] as const,
  fallback: 'unauthed',
  key: 'da.companion.lifecycle'
})

export const $companionLifecycle = lifecyclePersisted.$atom
export const setCompanionLifecycle = lifecyclePersisted.set

const $localSpriteState = atom<SpriteStateName>('idle')
const $desktopCompanionActivity = atom<DesktopCompanionActivityState>('idle')
const $previousState = atom<SpriteStateName>('idle')

// 跨模块共享的水合去重缓存：同 key 的并发水合只跑一次。
const inFlightHydrations = new Map<string, Promise<unknown>>()

// 打扰档位门控主动外发与主动推理（用户主动行为不被门控）：still 停止主动 LLM、normal 轻互动、autonomous 开放视觉与空间表达。
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

// null 表示无覆盖，生效档位回退 user_preferred；只有活动监视器（activity.ts）会写它。
export const $effectiveTierOverride = atom<DisturbanceTier | null>(null)

// 临时安静：截止前生效档位为静止，到期只清截止时间不改偏好；截止时间只存本机不上云，登出不清除。
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
    userPreferredTierPersisted.reload()
  }

  if (key === QUIET_UNTIL_KEY || key === null) {
    applyQuietUntil(readStoredQuietUntil())
  }
}

// 加载时按保存的截止时间恢复临时安静，重启不中断。
applyQuietUntil(readStoredQuietUntil())

// 手动静止与临时安静是硬锁定，override 只在两者都不成立时生效。
export const $effectiveTier = computed(
  [$userPreferredTier, $effectiveTierOverride, $quietUntil],
  (preferred, override, quietUntil) =>
    preferred === 'still' || quietUntil !== null ? 'still' : (override ?? preferred)
)

// 表现状态机优先级（DESIGN「状态与播放优先级」）。
const STATE_PRIORITY: Record<SpriteStateName, number> = {
  ...DESKTOP_COMPANION_ACTIVITY_PRIORITY,
  emotional: 35,
  interacting: 80
}

// 瞬态经 $previousState 与计时器自动恢复，因此绕过优先级门控，避免 WORKING/SPEAKING 压制瞬时提示。
const TRANSIENT_STATES: ReadonlySet<SpriteStateName> = new Set(['emotional', 'interacting'])

// 远端持续活动参与呈现裁决，不进入本地瞬态的恢复目标。
export const $spriteState = computed(
  [$localSpriteState, $desktopCompanionActivity],
  (local, remote): SpriteStateName => {
    if (TRANSIENT_STATES.has(local)) {
      return local
    }

    return STATE_PRIORITY[local] >= STATE_PRIORITY[remote] ? local : remote
  }
)

export function setDesktopCompanionActivity(state: DesktopCompanionActivityState): void {
  $desktopCompanionActivity.set(state)
}

let transientTimer: ReturnType<typeof setTimeout> | null = null
let activityCounter = 0
let activityResetTimer: ReturnType<typeof setTimeout> | null = null

function clearTransientTimer(): void {
  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }
}

export function setSpriteState(name: SpriteStateName, options?: SetSpriteStateOptions): void {
  const current = $localSpriteState.get()

  if (!options?.force && STATE_PRIORITY[name] < STATE_PRIORITY[current] && !TRANSIENT_STATES.has(name)) {
    // 低优先级不能打断高优先级，瞬时状态除外（经计时器自动恢复）。
    return
  }

  if (TRANSIENT_STATES.has(name)) {
    if (!TRANSIENT_STATES.has(current)) {
      $previousState.set(current)
    }

    $localSpriteState.set(name)
    clearTransientTimer()

    transientTimer = setTimeout(() => {
      transientTimer = null
      restoreAfterTransient()
    }, options?.durationMs ?? 1800)

    return
  }

  clearTransientTimer()
  $localSpriteState.set(name)
}

// 若瞬时过程中有更高优先级状态到达，优先取当前状态。
function restoreAfterTransient(): void {
  const currentAfter = $localSpriteState.get()
  const storedPrev = $previousState.get()

  const target = !TRANSIENT_STATES.has(currentAfter)
    ? currentAfter
    : TRANSIENT_STATES.has(storedPrev)
      ? 'idle'
      : storedPrev

  $localSpriteState.set(target)
}

// 提前结束仍在进行的指定瞬态（如表达片段收尾），恢复仍有效的持续状态。
export function endTransientState(name: SpriteStateName): void {
  if ($localSpriteState.get() !== name || !transientTimer) {
    return
  }

  clearTransientTimer()
  restoreAfterTransient()
}

// 拖拽期间持续保持 interacting（撤销在途瞬态计时器），松手时由带时长的 setSpriteState('interacting') 恢复。
export function holdInteracting(): void {
  const current = $localSpriteState.get()

  clearTransientTimer()

  if (!TRANSIENT_STATES.has(current)) {
    $previousState.set(current)
  }

  $localSpriteState.set('interacting')
}

export function reportUserActivity(): void {
  const current = $localSpriteState.get()

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

    if ($localSpriteState.get() === 'working') {
      // working(70) 盖住 idle(10)，不带 force 会被优先级门控吞掉，必须强制退出。
      setSpriteState('idle', { force: true })
    }
  }, 10000)
}

// 生效档位（含活动覆盖与临时安静）经配置管道上云，是后端闸门的唯一档位来源；与用户偏好分键（设备派生不回写偏好），只由精灵窗推送。
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

// 清掉瞬态/活动计时器（登出后 orphan 计时器会写 $spriteState）；必须在文件末尾，闭包按引用捕获上述变量，提前声明会在 HMR 时撞 TDZ。
registerStorageClearHandler(() => {
  clearTransientTimer()

  if (activityResetTimer) {
    clearTimeout(activityResetTimer)
    activityResetTimer = null
  }

  inFlightHydrations.clear()

  activityCounter = 0
  $localSpriteState.set('idle')
  $desktopCompanionActivity.set('idle')
  $previousState.set('idle')
  $effectiveTierOverride.set(null)
})
