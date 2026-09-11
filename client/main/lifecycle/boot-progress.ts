import { clampBootProgress, type DesktopBootProgress, IPC } from '@ipc/contracts'
import type { BrowserWindow } from 'electron'

import { sendToMain } from '../shared/utils'

interface BootProgressOptions {
  getMainWindow: () => BrowserWindow | null
  rememberLog: (chunk: string) => void
}

// 启动进度状态机：阶段、消息、百分比。渲染层通过 IPC 事件订阅 `bootProgress`
// 拿到最新快照，用于显示加载覆盖层。
export function createBootProgressMachine({ getMainWindow, rememberLog }: BootProgressOptions) {
  let state: DesktopBootProgress = {
    error: null,
    message: 'Waiting to start 唤生 backend',
    phase: 'idle',
    progress: 0,
    running: false,
    timestamp: Date.now()
  }

  function broadcast(): void {
    sendToMain(getMainWindow(), IPC.event.bootProgress, state)
  }

  function update(update: Partial<DesktopBootProgress>, options: { allowDecrease?: boolean } = {}): void {
    const nextProgressRaw = typeof update.progress === 'number' ? clampBootProgress(update.progress) : state.progress

    const nextProgress = options.allowDecrease ? nextProgressRaw : Math.max(state.progress, nextProgressRaw)

    state = {
      ...state,
      ...update,
      error: update.error === undefined ? state.error : update.error,
      progress: nextProgress,
      timestamp: Date.now()
    }

    if (update.message) {
      rememberLog(`[boot] ${update.message}`)
    }

    broadcast()
  }

  function advance(phase: string, message: string, progress: number): void {
    update({
      error: null,
      message,
      phase,
      progress,
      running: true
    })
  }

  return {
    advance,
    broadcast,
    getState: () => state,
    update
  }
}
