import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import { buildPrefsHydratedFromConfig } from '../shared/lib/config-sync'
import * as store from '../shared/lib/runner-config-store'
import { broadcastToAllWindows, errorMessage } from '../shared/utils'

interface PrefsIpcDeps {
  ipcMain: IpcMain
  log: (chunk: string) => void
}

// 伙伴偏好写穿透终点：点键合入配置镜像，乘既有管道（镜像原子写 + runner 推送 + 云端防抖上云，见 shared/lib/config-sync.ts）。只放行 companion.*；主题、快捷键与语言走各自带校验的通道，terminal 等本机节不允许经此写入。
const ALLOWED_KEY_PREFIX = 'companion.'

export function registerPrefsIpc({ ipcMain, log }: PrefsIpcDeps): void {
  ipcMain.on(IPC.send.prefsSet, (_event, payload: unknown) => {
    if (!payload || typeof payload !== 'object') {
      return
    }

    const { key, value } = payload as { key?: unknown; value?: unknown }

    if (typeof key !== 'string' || !key.startsWith(ALLOWED_KEY_PREFIX)) {
      return
    }

    const keyPath = key.split('.')

    if (keyPath.some(part => part.length === 0)) {
      return
    }

    void store
      .patch(keyPath, { value })
      .then(result => {
        if (!result.ok) {
          log(`[prefs] set ${key} failed: ${result.error || 'unknown'}`)

          return
        }

        // 降低透明度须所有窗口即时生效：沿用水合广播的形状，只带该字段与当前语言。
        if (key === 'companion.reduce_transparency' && typeof value === 'boolean') {
          const { accountId, language } = buildPrefsHydratedFromConfig(store.read())
          broadcastToAllWindows(IPC.event.prefsHydrated, {
            accountId,
            companion: { reduce_transparency: value },
            language
          })
        }
      })
      .catch(error => log(`[prefs] set ${key} failed: ${errorMessage(error)}`))
  })
}
