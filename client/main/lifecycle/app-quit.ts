import { setTimeout as delay } from 'node:timers/promises'

import type { App } from 'electron'

import { errorMessage } from '../shared/utils'

const RUNNER_STOP_TIMEOUT_MS = 3000

interface AppQuitDeps {
  app: Pick<App, 'exit' | 'on'>
  cleanupShortcuts: () => void
  destroyTray: () => void
  flushConfig: () => Promise<void>
  flushLog: () => void
  flushPlayback: () => Promise<void>
  log: (chunk: string) => void
  /** 停止 Runner；没有 Runner 时立即完成。 */
  stopRunner: () => Promise<unknown>
}

export interface AppQuit {
  isQuitting: () => boolean
  /** 在 before-quit 之前就要放行窗口关闭时调用（如原生 quitAndInstall 先关窗）。 */
  markQuitting: () => void
}

/** 退出链：before-quit 置退出标志、释放托盘与快捷键、尽力上云并落盘日志；will-quit 有界等待 Runner 收尾后 app.exit(0)，避免孤儿子进程，超时仍可能残留。 */
export function installAppQuit(deps: AppQuitDeps): AppQuit {
  let quitting = false
  let willQuitCleanupDone = false

  deps.app.on('before-quit', () => {
    quitting = true
    deps.destroyTray()
    deps.cleanupShortcuts()

    // 尽力上云，进程可能先退出：未上传的编辑只在云端缺少该键时由下次水合补传，云端已有的键以云端值为准。
    void deps.flushConfig()

    deps.flushLog()
  })

  deps.app.on('will-quit', event => {
    if (willQuitCleanupDone) {
      return
    }

    willQuitCleanupDone = true
    event.preventDefault()

    void Promise.race([
      Promise.all([
        deps.stopRunner().catch(error => {
          deps.log(`[runner-bridge] quit cleanup failed: ${errorMessage(error)}`)
        }),
        deps.flushPlayback().catch(error => {
          deps.log(`[voice-playback] quit flush failed: ${errorMessage(error)}`)
        })
      ]),
      delay(RUNNER_STOP_TIMEOUT_MS, undefined, { ref: false })
    ]).then(() => {
      deps.flushLog()
      deps.app.exit(0)
    })
  })

  deps.app.on('window-all-closed', () => {
    // 常驻托盘：关窗不退出；真正退出由托盘/菜单的 app.quit 驱动，这里不能再调 app.quit，否则重入清理并双跑 runner stop。
  })

  return {
    isQuitting: () => quitting,
    markQuitting: () => {
      quitting = true
    }
  }
}
