import path from 'node:path'

import type { App } from 'electron'

import type { SurfaceCompanionPreference } from '@ipc/contracts'

import { atomicWriteFile, safeReadJson } from '../shared/utils'

export const PANEL_SIZE = { height: 720, minHeight: 540, minWidth: 800, width: 1280 }

const DEFAULT_COMPANION: SurfaceCompanionPreference = { enabled: true, side: 'right' }

export function parseCompanionPreference(raw: unknown): SurfaceCompanionPreference | null {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const value = raw as Partial<SurfaceCompanionPreference>

  return typeof value.enabled === 'boolean' && (value.side === 'left' || value.side === 'right')
    ? { enabled: value.enabled, side: value.side }
    : null
}

export function createSurfaceCompanionPreferences(app: Pick<App, 'getPath'>): {
  get: () => SurfaceCompanionPreference
  set: (next: SurfaceCompanionPreference) => Promise<void>
} {
  const file = path.join(app.getPath('userData'), 'main-window-companion.json')
  let preference = parseCompanionPreference(safeReadJson<unknown>(file)) ?? DEFAULT_COMPANION

  return {
    get: () => ({ ...preference }),
    set: async next => {
      await atomicWriteFile(file, JSON.stringify(next, null, 2))
      preference = { ...next }
    }
  }
}
