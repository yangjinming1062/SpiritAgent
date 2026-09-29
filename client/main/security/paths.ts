import os from 'node:os'
import path from 'node:path'

function spiritagentHome(): string {
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')

    return path.join(local, 'SpiritAgent')
  }

  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'SpiritAgent')
  }

  return path.join(os.homedir(), '.spiritagent')
}

/** 桌面进程的 Home：`SPIRITAGENT_DESKTOP_USER_DATA_DIR` 覆盖时取 `<override>/spiritagent-home`，否则为平台默认位置。 */
export function resolveDesktopHome(): string {
  const override = process.env.SPIRITAGENT_DESKTOP_USER_DATA_DIR

  if (override) {
    return path.join(path.resolve(override), 'spiritagent-home')
  }

  return spiritagentHome()
}
