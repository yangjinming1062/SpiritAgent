import type { HapticInput, TriggerOptions } from 'web-haptics'

import { $hapticsMuted } from '@/shared/store/haptics'

type HapticIntent =
  | 'cancel'
  | 'close'
  | 'crisp'
  | 'error'
  | 'open'
  | 'selection'
  | 'streamDone'
  | 'streamStart'
  | 'submit'
  | 'success'
  | 'tap'
  | 'warning'

interface HapticConfig {
  options?: TriggerOptions
  pattern: HapticInput
}

const airyTap = [{ duration: 16, intensity: 0.52 }]

const crispTap = [{ duration: 10, intensity: 0.92 }]

const friendlySuccess = [
  { duration: 28, intensity: 0.5 },
  { delay: 42, duration: 30, intensity: 0.68 },
  { delay: 48, duration: 38, intensity: 0.86 }
]

const softArrive = [
  { duration: 18, intensity: 0.42 },
  { delay: 36, duration: 22, intensity: 0.66 }
]

const softLeave = [
  { duration: 22, intensity: 0.58 },
  { delay: 32, duration: 16, intensity: 0.34 }
]

const HAPTIC_INTENTS: Record<HapticIntent, HapticConfig> = {
  cancel: {
    pattern: [
      { duration: 34, intensity: 0.72 },
      { delay: 54, duration: 26, intensity: 0.38 }
    ]
  },
  close: { pattern: softLeave },
  crisp: { pattern: crispTap },
  error: {
    pattern: [
      { duration: 34, intensity: 0.82 },
      { delay: 42, duration: 34, intensity: 0.72 },
      { delay: 58, duration: 44, intensity: 0.86 }
    ]
  },
  open: { pattern: softArrive },
  selection: { pattern: airyTap },
  streamDone: { pattern: friendlySuccess },
  streamStart: { pattern: [{ duration: 10, intensity: 0.32 }] },
  submit: {
    pattern: [
      { duration: 24, intensity: 0.58 },
      { delay: 48, duration: 36, intensity: 0.82 }
    ]
  },
  success: { pattern: friendlySuccess },
  tap: {
    pattern: [
      { duration: 14, intensity: 0.58 },
      { delay: 30, duration: 12, intensity: 0.42 }
    ]
  },
  warning: {
    pattern: [
      { duration: 34, intensity: 0.64 },
      { delay: 84, duration: 42, intensity: 0.5 }
    ]
  }
}

type HapticTrigger = (input?: HapticInput, options?: TriggerOptions) => Promise<void> | undefined

let registeredTrigger: HapticTrigger | null = null
let lastSelectionAt = 0

// 全局滚动速率限制。上游失控循环（鉴权过期错误 toast 风暴、重连抖动）可能
// 一秒内请求几十次触感，触控板执行器会发出令人焦虑的"咔哒"震动。把触发频率
// 限制在 RATE_WINDOW 内最多 RATE_LIMIT 次，防止任何源头对执行器扫射；
// 正常 UI 触感由人手控制，远低于该上限。
const RATE_WINDOW = 1000
const RATE_LIMIT = 5
let recentFires: number[] = []

export function registerHapticTrigger(trigger: HapticTrigger | null): void {
  registeredTrigger = trigger
}

export function triggerHaptic(intent: HapticIntent = 'selection'): void {
  if ($hapticsMuted.get() || !registeredTrigger) {
    return
  }

  const now = performance.now()

  if (intent === 'selection') {
    if (now - lastSelectionAt < 50) {
      return
    }

    lastSelectionAt = now
  }

  recentFires = recentFires.filter(t => now - t < RATE_WINDOW)

  if (recentFires.length >= RATE_LIMIT) {
    return
  }

  recentFires.push(now)

  const config = HAPTIC_INTENTS[intent]

  void registeredTrigger(config.pattern, config.options)?.catch(() => undefined)
}
