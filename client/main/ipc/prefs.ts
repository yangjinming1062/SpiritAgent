import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

interface PrefsIpcDeps {
  ipcMain: IpcMain
  log: (chunk: string) => void
  onReduceTransparencyChanged?: (value: boolean) => void
}

// 渲染层伙伴偏好写穿透终点：把点键合入配置镜像，乘既有管道
// （镜像原子写 + runner 推送 + 云端防抖上云，见 shared/lib/config-sync.ts）。
// 只放行 companion.*；主题、快捷键与语言走各自带校验的通道，terminal 等本机节不允许经此写入。
const ALLOWED_KEY_PREFIX = 'companion.'

export function registerPrefsIpc({ ipcMain, log, onReduceTransparencyChanged }: PrefsIpcDeps): void {
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

        if (key === 'companion.reduce_transparency' && typeof value === 'boolean') {
          onReduceTransparencyChanged?.(value)
        }
      })
      .catch(error => log(`[prefs] set ${key} failed: ${errorMessage(error)}`))
  })
}
