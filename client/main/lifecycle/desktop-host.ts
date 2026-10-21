import type { DesktopHost, DesktopHostOptions } from '../shared/desktop-host'

import { createMacosDesktopHost } from './desktop-macos-host'
import { createExplorerDesktopHost } from './explorer-desktop-host'

export function createDesktopHost(options: DesktopHostOptions): DesktopHost {
  if (process.platform === 'darwin') {
    return createMacosDesktopHost(options)
  }

  return {
    ...createExplorerDesktopHost(options),
    // Windows 缺组件仍沿用进入时的诊断，不改变原有入口可见性。
    availability: () => ({
      supported: process.platform === 'win32',
      reason: process.platform === 'win32' ? null : '当前系统不支持桌面模式。'
    }),
    accessibility: () => ({ granted: process.platform === 'win32', requestable: false }),
    requestAccessibility: () => {}
  }
}
