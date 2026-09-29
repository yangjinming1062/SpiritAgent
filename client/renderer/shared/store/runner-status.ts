import type { DesktopRunnerPhase, DesktopRunnerStatusEvent } from '@ipc/contracts'
import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

// 「Runner 网桥是否在线」的唯一真源。沿用 hydrateAuth + applyAuthBroadcast 的模式：
// 一个 IPC 同步 getter 覆盖「我们订阅前网桥就已经跑起来」的情况
//（Electron IPC 没有事件重放），一份订阅把后续转换写入 atom。
// 消费方订阅 $runnerPhase 监听转换——无需每个消费方各自跳一次同步 getter。
// 见 modules/character 的 activity.ts 与 autonomy.ts。
export const $runnerPhase = atom<DesktopRunnerPhase>('idle')

let offRunnerStatus: (() => void) | null = null

export async function hydrateRunnerStatus(): Promise<void> {
  const desktop = window.spiritagent

  // 同步 getter 优先——消除「订阅太晚，错过初始 running 事件」的窗口期。
  // 若网桥尚未创建，处理函数返回 { phase: 'idle' }，这也是一个有效的早期回答。
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
