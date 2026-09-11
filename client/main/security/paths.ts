import os from 'node:os'
import path from 'node:path'

export function spiritagentHome(): string {
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')

    return path.join(local, 'SpiritAgent')
  }

  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'SpiritAgent')
  }

  return path.join(os.homedir(), '.spiritagent')
}
