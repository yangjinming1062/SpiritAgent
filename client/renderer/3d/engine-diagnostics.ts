import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

import type { PowerProfile } from './PowerProfile'
import type { EngineBackendKind } from './types'

// 渲染引擎可观测性：实际启动的降级档位、当前功率档位，以及测得的帧率。
// 由开发者 overlay 消费；档位切换还会落到桌面日志里，纯靠日志就能排查功率回退。

export const $rendererBackend = atom<EngineBackendKind | null>(null)
export const $powerProfile = atom<PowerProfile>('active')
export const $engineFps = atom(0)

// 渲染守卫 —— 把不可恢复的引擎错误抛到表面，让 ticker 停掉，而不是陷入逐帧抛异常的循环。
export const $engineError = atom<{ message: string; at: number } | null>(null)

let lastLoggedProfile: PowerProfile | null = null

export function reportBackend(kind: EngineBackendKind): void {
  $rendererBackend.set(kind)
}

export function reportFrameStats(profile: PowerProfile, fps: number): void {
  $powerProfile.set(profile)
  $engineFps.set(fps)

  if (profile !== lastLoggedProfile) {
    lastLoggedProfile = profile
    log.info('engine', `power profile -> ${profile} (observed ${fps.toFixed(0)} fps)`)
  }
}

export function reportEngineError(message: string): void {
  $engineError.set({ message, at: Date.now() })
  log.error('engine', `engine error: ${message}`)
}
