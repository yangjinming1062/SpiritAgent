import path from 'node:path'

import type { App, Rectangle } from 'electron'

import type { SurfaceCompanionPreference, SurfaceCompanionSide } from '@ipc/contracts'

import { atomicWriteFile, safeReadJson } from '../shared/utils'

export const PANEL_SIZE = { height: 720, minHeight: 540, minWidth: 800, width: 1280 }

const DEFAULT_COMPANION: SurfaceCompanionPreference = { enabled: true, side: 'right' }

export function preferredCompanionWidth(height: number): number {
  return Math.round((height * 0.58 * 125) / 275)
}

export function companionSlotMinimum(desired: number): number {
  return Math.ceil(desired * 0.65)
}

/** 可用宽度内取陪伴栏宽度，不足收纳阈值时收为 0。 */
export function companionSlotWidth(desired: number, available: number): number {
  const width = Math.min(desired, Math.max(0, Math.floor(available)))

  return width >= companionSlotMinimum(desired) ? width : 0
}

export function companionSlot(
  panel: Rectangle,
  side: SurfaceCompanionSide,
  enabled: boolean,
  workArea: Rectangle
): { width: number; reason: 'edge' | null } {
  if (!enabled) {
    return { reason: null, width: 0 }
  }

  const desired = preferredCompanionWidth(panel.height)

  const available = side === 'left' ? panel.x - workArea.x : workArea.x + workArea.width - panel.x - panel.width

  const width = companionSlotWidth(desired, available)

  return width > 0 ? { reason: null, width } : { reason: 'edge', width: 0 }
}

export function outerBounds(panel: Rectangle, side: SurfaceCompanionSide, slotWidth: number): Rectangle {
  return {
    height: panel.height,
    width: panel.width + slotWidth,
    x: panel.x - (side === 'left' ? slotWidth : 0),
    y: panel.y
  }
}

export function panelBounds(outer: Rectangle, side: SurfaceCompanionSide, slotWidth: number): Rectangle {
  return {
    height: outer.height,
    width: outer.width - slotWidth,
    x: outer.x + (side === 'left' ? slotWidth : 0),
    y: outer.y
  }
}

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
