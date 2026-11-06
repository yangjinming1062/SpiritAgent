import path from 'node:path'

import { readMacosApplicationIcon } from '../macos-application-target'
import { readWindowsApplicationIcon } from '../windows-application-icon'

export async function applicationIcon(target: string, iconPath?: string | null): Promise<string | null> {
  return process.platform === 'darwin'
    ? readMacosApplicationIcon(target)
    : readWindowsApplicationIcon(target, iconPath ?? undefined)
}

export async function jumpListTaskIcon(iconPath: string | null, iconIndex: number): Promise<string | null> {
  if (!iconPath || !path.isAbsolute(iconPath)) {
    return null
  }

  return readWindowsApplicationIcon(iconPath, iconPath, iconIndex)
}
