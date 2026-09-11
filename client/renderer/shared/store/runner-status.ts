import type { DesktopRunnerPhase, DesktopRunnerStatusEvent } from '@ipc/contracts'
import { atom } from 'nanostores'

// 「Runner 网桥是否在线」的唯一真源。沿用 hydrateAuth + applyAuthBroadcast 的模式：
// 一个 IPC 同步 getter 覆盖「我们订阅前网桥就已经跑起来」的情况
//（Electron IPC 没有事件重放），一份订阅把后续转换写入 atom。
// 消费方订阅 $runnerPhase 监听转换——无需每个消费方各自跳一次同步 getter。
// 见 companion/activity.ts 和 hub/settings/speech-settings.tsx。
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
  } catch {
    // 网桥探测失败（旧版 preload / IPC 传输错误）。下方订阅作为回退路径。
  }

  // 后续转换。幂等：订阅已挂载时再次调用 hydrate 只是重新跑一次同步 getter。
  if (offRunnerStatus) {
    return
  }

  offRunnerStatus =
    desktop.onRunnerStatus?.((ev: DesktopRunnerStatusEvent) => {
      if (ev.type === 'running' || ev.type === 'runner_ready') {
        $runnerPhase.set('running')
      } else if (ev.type === 'stopped') {
        $runnerPhase.set('stopped')
      } else {
        $runnerPhase.set('stopped')
      }
    }) ?? null
}
