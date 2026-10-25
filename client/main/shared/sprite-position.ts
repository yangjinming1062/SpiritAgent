import path from 'node:path'

import type { DesktopSpriteRestPosition } from '@ipc/contracts'

import { isFiniteNumber, safeReadJson } from './utils'

const POSITION_FILE = 'companion-position.json'

/** 精灵静止位置文件路径；读写两侧共用，文件名只在本模块定义。 */
export function spritePositionPath(userDataDir: string): string {
  return path.join(userDataDir, POSITION_FILE)
}

export function readRestPosition(userDataDir?: string): null | DesktopSpriteRestPosition {
  if (!userDataDir) {
    return null
  }

  const parsed = safeReadJson<{ origin?: unknown; x?: unknown; y?: unknown }>(spritePositionPath(userDataDir))

  if (!parsed || !isFiniteNumber(parsed.x) || !isFiniteNumber(parsed.y)) {
    return null
  }

  const next: DesktopSpriteRestPosition = { x: parsed.x, y: parsed.y }
  const origin = parsed.origin as { x?: unknown; y?: unknown } | null

  if (origin && isFiniteNumber(origin.x) && isFiniteNumber(origin.y)) {
    next.origin = { x: origin.x, y: origin.y }
  }

  return next
}
