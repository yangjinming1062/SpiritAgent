import path from 'node:path'

import type { App, Rectangle } from 'electron'

import type { SurfaceCompanionPreference, SurfaceCompanionSide, SurfaceId } from '@ipc/contracts'

import { atomicWriteFile, safeReadJson } from '../shared/utils'

export const PANEL_SIZES: Record<SurfaceId, { height: number; minHeight: number; minWidth: number; width: number }> = {
  living: { height: 720, minHeight: 540, minWidth: 960, width: 1280 },
  workbench: { height: 800, minHeight: 640, minWidth: 1095, width: 1321 }
}

const DEFAULT_COMPANIONS: Record<SurfaceId, SurfaceCompanionPreference> = {
  living: { enabled: true, side: 'right' },
  workbench: { enabled: true, side: 'left' }
}

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
  get: (id: SurfaceId) => SurfaceCompanionPreference
  set: (id: SurfaceId, next: SurfaceCompanionPreference) => Promise<void>
} {
  const file = path.join(app.getPath('userData'), 'surface-companions.json')
  const saved = safeReadJson<Record<string, unknown>>(file)

  let preferences = {
    living: parseCompanionPreference(saved?.living) ?? DEFAULT_COMPANIONS.living,
    workbench: parseCompanionPreference(saved?.workbench) ?? DEFAULT_COMPANIONS.workbench
  }

  return {
    get: id => ({ ...preferences[id] }),
    set: async (id, next) => {
      const updated = { ...preferences, [id]: next }
      await atomicWriteFile(file, JSON.stringify(updated, null, 2))
      preferences = updated
    }
  }
}
