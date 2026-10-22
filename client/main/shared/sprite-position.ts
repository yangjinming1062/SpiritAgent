import path from 'node:path'

import { type DesktopSpritePosition, type DesktopSpriteRestPosition } from '@ipc/contracts'
import { clamp } from '@runtime'

import { isFiniteNumber, safeReadJson } from './utils'

const POSITION_FILE = 'companion-position.json'

/** 精灵静止位置文件路径；读写两侧共用，文件名只在本模块定义。 */
export function spritePositionPath(userDataDir: string): string {
  return path.join(userDataDir, POSITION_FILE)
}

// 落盘与读盘共用：贴边侧合法且纵向比例为有限数时保留，比例限制在 0 到 1。
export function normalizeScreenEdge(value: unknown): DesktopSpritePosition['screenEdge'] {
  const edge = value as { side?: unknown; yRatio?: unknown } | null | undefined

  return edge && (edge.side === 'left' || edge.side === 'right') && isFiniteNumber(edge.yRatio)
    ? { side: edge.side, yRatio: clamp(edge.yRatio, 0, 1) }
    : undefined
}

export function readRestPosition(userDataDir?: string): null | DesktopSpriteRestPosition {
  if (!userDataDir) {
    return null
  }

  const parsed = safeReadJson<{ origin?: unknown; screenEdge?: unknown; x?: unknown; y?: unknown }>(
    spritePositionPath(userDataDir)
  )

  if (!parsed || !isFiniteNumber(parsed.x) || !isFiniteNumber(parsed.y)) {
    return null
  }

  const next: DesktopSpriteRestPosition = { x: parsed.x, y: parsed.y }
  const origin = parsed.origin as { x?: unknown; y?: unknown } | null

  if (origin && isFiniteNumber(origin.x) && isFiniteNumber(origin.y)) {
    next.origin = { x: origin.x, y: origin.y }
  }

  const screenEdge = normalizeScreenEdge(parsed.screenEdge)

  if (screenEdge) {
    next.screenEdge = screenEdge
  }

  return next
}
