import type { DesktopRunnerPhase, DesktopRunnerStatusEvent } from '@ipc/contracts'
import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

// 「Runner 网桥是否在线」的唯一真源。沿用 hydrateAuth + applyAuthBroadcast 模式：IPC 同步 getter 覆盖「订阅前网桥已跑起来」（Electron IPC 无事件重放），一份订阅把后续转换写入 atom；消费方订阅 $runnerPhase，无需各自跳同步 getter。
export const $runnerPhase = atom<DesktopRunnerPhase>('idle')

let offRunnerStatus: (() => void) | null = null

export async function hydrateRunnerStatus(): Promise<void> {
  const desktop = window.spiritagent

  // 同步 getter 优先——消除「订阅太晚错过初始 running 事件」的窗口期；网桥尚未创建时返回 { phase: 'idle' }，也是有效的早期回答。
  try {
    const state = await desktop.runnerGetState?.()

    if (state?.phase) {
      $runnerPhase.set(state.phase)
    }
  } catch (error) {
    // 失败时 $runnerPhase 保持原值，直到下一次状态事件。
    log.warn('runner-status', 'runnerGetState failed', error)
  }

  // 后续转换。幂等：订阅已挂载时再次调用 hydrate 只是重新跑一次同步 getter。
  if (offRunnerStatus) {
    return
  }

  offRunnerStatus =
    desktop.onRunnerStatus?.((ev: DesktopRunnerStatusEvent) => {
      $runnerPhase.set(
        ev.type === 'running' || ev.type === 'runner_ready'
          ? 'running'
          : ev.type === 'stopping'
            ? 'stopping'
            : 'stopped'
      )
    }) ?? null
}
