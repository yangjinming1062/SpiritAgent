import type { DesktopVersionInfo } from '@ipc/contracts'
import { atom } from 'nanostores'

// 首次读取时懒填充。由 `refreshDesktopVersion()` 刷新（由 About 面板挂载时调用，
// 这样即使刚重新启动也能展示当前运行的版本）。
const $desktopVersion = atom<DesktopVersionInfo | null>(null)

async function refreshDesktopVersion(): Promise<void> {
  try {
    const next = await window.spiritagent?.getVersion()

    if (next) {
      $desktopVersion.set(next)
    }
  } catch {
    // 尽力而为；About 面板会展示「版本不可用」的提示。
  }
}

export { $desktopVersion, refreshDesktopVersion }
