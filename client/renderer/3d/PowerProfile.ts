import type { SpriteStateName } from '@/companion'

// 常驻伙伴窗口的渲染功耗档位。渲染进程关闭了 Chromium 限流（用于后台聊天流），
// 因此 3D 循环改为依据这些信号自行节流。

export type PowerProfile = 'active' | 'idle' | 'dormant'

export interface PowerSignals {
  spriteState: SpriteStateName
  screenLocked: boolean
  documentHidden: boolean
  fullscreen: boolean
  staticCovered: boolean
  modelSettled: boolean
}

// active 档位以 60fps 运行（交互/播报/情绪），idle 以 30fps 运行（日常待机/呼吸），
// 保证顺滑的同时大幅降低常驻功耗与 GPU/CPU 占用。
// dormant 在锁屏/隐藏/全屏/休眠时由定时器以 4fps 驱动，以最大程度节省功耗。
export const PROFILE_FPS: Record<PowerProfile, number> = { active: 60, idle: 30, dormant: 4 }

export function resolvePowerProfile(signals: PowerSignals): PowerProfile {
  // 就绪守卫：在首个角色模型稳定之前，孵化阶段必须全速运行——
  // 如果用 dormant/idle 启动，GLB 解析与贴图上传会被拖到 250ms 一帧。
  if (!signals.modelSettled) {
    return 'active'
  }

  const dormant = signals.screenLocked || signals.documentHidden || signals.fullscreen || signals.staticCovered

  if (dormant) {
    return 'dormant'
  }

  return signals.spriteState === 'idle' || signals.spriteState === 'disconnected' ? 'idle' : 'active'
}
