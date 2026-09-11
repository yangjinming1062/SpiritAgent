import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import { definePersistedEnum, registerStorageClearHandler } from '@/shared/lib/storage'

// 渲染层按 unauthed → onboarding（向导进行中）→ ready（向导完成后）流转。
export type CompanionLifecycle = 'unauthed' | 'onboarding' | 'ready'

// 第二阶段状态机（DESIGN §2）。
export type SpriteStateName =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'emotional'
  | 'interacting'
  | 'disconnected'

export type SpriteEmotion = string

export const BUILTIN_EMOTIONS: ReadonlySet<string> = new Set([
  'happy',
  'sad',
  'surprised',
  'excited',
  'confused',
  'concerned',
  'shy',
  'proud',
  'grateful',
  'playful',
  'bored',
  'lonely',
  'sleepy',
  'curious',
  'embarrassed',
  'apologetic',
  'pout',
  'angry',
  'smug',
  'scared',
  'relieved'
])

const lifecyclePersisted = definePersistedEnum<CompanionLifecycle>({
  allowed: ['unauthed', 'ready', 'onboarding'] as const,
  fallback: 'unauthed',
  key: 'da.companion.lifecycle'
})

export const $companionLifecycle = lifecyclePersisted.$atom
export const setCompanionLifecycle = lifecyclePersisted.set

export const $spriteState = atom<SpriteStateName>('idle')
export const $spriteEmotion = atom<SpriteEmotion | null>(null)
// 可选的结构化动作提示（如 turn_away），用于细化情绪片段。
export const $spriteAction = atom<string | null>(null)
// 动作序列编排队列：$spriteAction 恒为当前/首个动作（3D 只消费单值），后续由 2D driver 逐个推进。
export const $spriteActionQueue = atom<string[]>([])
const $previousState = atom<SpriteStateName>('idle')
export const $clipOverride = atom<string | null>(null)

// 打扰档位门控伙伴的主动行为（DESIGN §6.2）。
// 三档：still（静止，停止一切主动 LLM 调用与分析，仅响应交互）、
// normal（常规，仅文字问候等原地轻互动）、autonomous（自主，开放桌面精灵视觉与空间表达）。
// 用户主动行为永不被门控——只门控主动外发（companion.message）与主动推理发起。
export type DisturbanceTier = 'still' | 'normal' | 'autonomous'

const userPreferredTierPersisted = definePersistedEnum<DisturbanceTier>({
  allowed: ['still', 'normal', 'autonomous'] as const,
  fallback: 'normal',
  key: 'da.companion.disturbanceTier',
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

// 手动静止是硬锁定：即便活动监视器在用户已选静止时写入 override，
// 渲染出的生效档位也保持静止。其他覆盖（normal / autonomous）
// 仅在用户未选静止时生效。
export const $effectiveTier = computed([$userPreferredTier, $effectiveTierOverride], (preferred, override) =>
  preferred === 'still' ? 'still' : (override ?? preferred)
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

// 这些状态通过 ``$previousState`` 与下方计时器自动恢复。
// 它们绕过优先级门控，避免进行中的 WORKING/SPEAKING 动画
// 压制一个瞬时的情绪/互动提示——新增瞬时状态只需要在这里加一项，
// 而不必改三个代码位置。
const TRANSIENT_STATES: ReadonlySet<SpriteStateName> = new Set(['emotional', 'interacting'])

let transientTimer: ReturnType<typeof setTimeout> | null = null
let activityCounter = 0
let activityResetTimer: ReturnType<typeof setTimeout> | null = null

export function setSpriteState(
  name: SpriteStateName,
  options?: { action?: string | null; durationMs?: number; emotion?: SpriteEmotion; force?: boolean }
): void {
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
    if (current !== 'emotional' && current !== 'interacting') {
      $previousState.set(current)
    }

    if (options?.emotion) {
      $spriteEmotion.set(options.emotion)
      $spriteAction.set(options.action ?? null)
    }

    $spriteState.set(name)

    if (transientTimer) {
      clearTimeout(transientTimer)
    }

    const ms = options?.durationMs ?? (name === 'emotional' ? 2500 : 1800)
    transientTimer = setTimeout(() => {
      transientTimer = null
      $spriteEmotion.set(null)
      $spriteAction.set(null)
      $spriteActionQueue.set([])
      // 若瞬时过程中有更高优先级状态到达，优先取当前状态。
      const currentAfter = $spriteState.get()
      const storedPrev = $previousState.get()

      const target =
        currentAfter !== 'emotional' && currentAfter !== 'interacting'
          ? currentAfter
          : storedPrev === 'emotional' || storedPrev === 'interacting'
            ? 'idle'
            : storedPrev

      $spriteState.set(target)
    }, ms)

    return
  }

  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  $spriteEmotion.set(options?.emotion ?? null)
  $spriteAction.set(options?.action ?? null)

  if (!options?.action) {
    $spriteActionQueue.set([])
  }

  $spriteState.set(name)
}

/** 播放动作序列：首个动作即刻进入 $spriteAction（3D 消费），后续进队列由 2D driver 推进。 */
export function playSpriteActionSequence(actions: readonly string[]): void {
  const [first, ...rest] = actions

  $spriteActionQueue.set(rest)
  $spriteAction.set(first ?? null)
}

// 程序化视线目标（精灵窗口归一 [-1,1]，与指针跟随同空间）：显式目标优先于指针
// （ritual walk 飞行途中锁定目标窗口中心）；null = 回到指针跟随。
export const $gazeTarget = atom<{ nx: number; ny: number } | null>(null)

let gazeResetTimer: ReturnType<typeof setTimeout> | null = null

/** 锁定视线到一个点，durationMs 后自动回到指针跟随。 */
export function lockGazeToPoint(point: { nx: number; ny: number }, durationMs = 6000): void {
  if (gazeResetTimer) {
    clearTimeout(gazeResetTimer)
  }

  $gazeTarget.set(point)
  gazeResetTimer = setTimeout(() => {
    $gazeTarget.set(null)
    gazeResetTimer = null
  }, durationMs)
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

// 生效档位（含活动覆盖）经配置管道上云，是后端闸门（主动消息 / cron / 视觉与空间推理）
// 的唯一档位来源；与用户偏好分键——生效值是设备派生的，不回写本地偏好。
export function pushEffectiveDisturbanceTier(tier: DisturbanceTier): void {
  window.spiritagent?.prefs?.set({ key: 'companion.disturbance_tier', value: tier })
}

export function resolveCompanionRenderLayer(opts: {
  glbLoadFailed: boolean
  modelStatus?: string
  puppetReady: boolean
  renderMode: '2d' | '3d'
}): 'companion3d' | 'puppet' {
  const modelFailed = opts.glbLoadFailed || opts.modelStatus === 'failed'
  const isModelReady = opts.renderMode === '3d' && !modelFailed && opts.modelStatus === 'succeeded'

  if (opts.renderMode === '2d' && opts.puppetReady) {
    return 'puppet'
  }

  if (isModelReady) {
    return 'companion3d'
  }

  if (opts.puppetReady) {
    return 'puppet'
  }

  return 'companion3d'
}

const inFlightHydrations = new Map<string, Promise<unknown>>()

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
  hydrateMesh2D: () => Promise<unknown>
  hydrateModel: () => Promise<unknown>
  hydratePersona: () => Promise<unknown>
  hydratePortrait?: () => Promise<unknown>
  hydratePuppet: () => Promise<unknown>
}): Promise<void> {
  const tasks = [
    runOnce('persona', deps.hydratePersona),
    runOnce('model', deps.hydrateModel),
    runOnce('mesh2d', deps.hydrateMesh2D)
  ]

  if (deps.hydratePortrait) {
    tasks.push(runOnce('portrait', deps.hydratePortrait))
  }

  try {
    await Promise.all(tasks)
    await runOnce('puppet', deps.hydratePuppet)
  } catch (err) {
    log.warn('companion-store', 'ensureCompanionHydrated failed', err)
  }
}

// 清掉所有瞬态/活动计时器与排队状态——登出后 orphan 计时器在新会话里会写 $spriteState。
// 必须在文件末尾：闭包按引用捕获 transientTimer / activityResetTimer / activityCounter / $previousState /
// $clipOverride / $effectiveTierOverride，提前声明会在 HMR 同步调用时撞 TDZ。
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
  $spriteEmotion.set(null)
  $spriteAction.set(null)
  $spriteActionQueue.set([])
  $spriteState.set('idle')
  $previousState.set('idle')
  $clipOverride.set(null)
  $effectiveTierOverride.set(null)
})
