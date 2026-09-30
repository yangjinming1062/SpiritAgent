import { IPC, SPIRITAGENT_UI_THEMES, type SpiritAgentUiTheme } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { broadcastToAllWindows } from '../shared/utils'

interface UiThemeIpcDeps {
  ipcMain: IpcMain
  log: (chunk: string) => void
}

// ui.theme 节漏斗进配置镜像随云端同步管道上云（localStorage 仍是各窗口即时缓存）；广播即时同步各窗口，不等待落盘，落盘失败只记日志。
export function registerUiThemeIpc({ ipcMain, log }: UiThemeIpcDeps): void {
  ipcMain.on(IPC.send.uiTheme, (_event, payload: SpiritAgentUiTheme) => {
    if (typeof payload !== 'string' || !SPIRITAGENT_UI_THEMES.includes(payload)) {
      return
    }

    void store
      .mutate(config => {
        config.ui = { ...(config.ui as Record<string, unknown> | undefined), theme: payload }
      })
      .then(result => {
        if (!result.ok) {
          log(`[ui-theme] saving ${payload} failed: ${result.error || 'unknown'}`)
        }
      })

    broadcastToAllWindows(IPC.event.uiThemeChanged, { theme: payload })
  })
}
