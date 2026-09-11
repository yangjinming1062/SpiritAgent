import { IPC, SPIRITAGENT_UI_THEMES, type SpiritAgentUiTheme } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { broadcastToAllWindows } from '../shared/utils'

interface UiThemeIpcDeps {
  ipcMain: IpcMain
}

// ui.theme 节漏斗进配置镜像，随云端同步管道上云（渲染层 localStorage 仍是各窗口的即时缓存）。
export function registerUiThemeIpc({ ipcMain }: UiThemeIpcDeps): void {
  ipcMain.on(IPC.send.uiTheme, (_event, payload: SpiritAgentUiTheme) => {
    if (typeof payload !== 'string' || !SPIRITAGENT_UI_THEMES.includes(payload)) {
      return
    }

    void store.mutate(config => {
      config.ui = { ...(config.ui as Record<string, unknown> | undefined), theme: payload }
    })

    broadcastToAllWindows(IPC.event.uiThemeChanged, { theme: payload })
  })
}
