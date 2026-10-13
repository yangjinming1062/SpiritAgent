import type { DesktopScreenRect } from '@ipc/contracts'
import { clamp } from '@runtime'

import type { SpatialPeek } from '../spatial-peek'

import type { Position, SpriteRect, ViewportSize } from './types'

export const REST_MARGIN = 24

/** 未缩放舞台尺寸，只随视口高度变化；落位与视频画布共用。 */
export function baseSpriteSize(viewportHeight: number): { width: number; height: number } {
  const height = Math.round(clamp(viewportHeight / 3, 260, 960))

  return { width: Math.round(height * 0.85), height }
}

export function spriteContentBox(viewport: ViewportSize, content: SpriteRect, scale: number): SpriteRect {
  const { width, height } = baseSpriteSize(viewport.height)

  return {
    left: content.left * width * scale,
    top: content.top * height * scale,
    right: content.right * width * scale,
    bottom: content.bottom * height * scale
  }
}

export function computeHomePosition(viewport: ViewportSize, content: SpriteRect, scale: number): Position {
  const box = spriteContentBox(viewport, content, scale)

  return {
    x: Math.max(REST_MARGIN, viewport.width - box.right - REST_MARGIN),
    y: Math.max(-box.top, viewport.height - box.bottom)
  }
}

// 普通落位约束内容轮廓，透明留白可以越界。
export function clampPositionToViewport(
  position: Position,
  viewport: ViewportSize,
  content: SpriteRect,
  scale: number
): Position {
  const box = spriteContentBox(viewport, content, scale)

  return {
    x: clamp(position.x, -box.left, Math.max(-box.left, viewport.width - box.right)),
    y: clamp(position.y, -box.top, Math.max(-box.top, viewport.height - box.bottom))
  }
}

export interface PerchPlacement {
  pos: { x: number; y: number }
  scale: number
}

/** 普通栖息选可容纳比例较大的一侧，同等空间优先右侧；低于 minScale 时放弃。 */
export function computePerchPlacement(
  geom: DesktopScreenRect,
  maxScale: number,
  viewport: ViewportSize,
  content: SpriteRect,
  minScale: number
): PerchPlacement | null {
  const margin = 8
  const { width: spriteW, height: spriteH0 } = baseSpriteSize(viewport.height)
  const contentLeft = content.left
  const contentTop = content.top
  const contentRight = content.right
  const contentBottom = content.bottom
  const contentW = Math.max(1, (contentRight - contentLeft) * spriteW)

  const rightAvail = Math.max(0, viewport.width - REST_MARGIN - (geom.x + geom.w) - margin)
  const leftAvail = Math.max(0, geom.x - margin - REST_MARGIN)
  const rightScale = Math.min(maxScale, rightAvail / contentW)
  const leftScale = Math.min(maxScale, leftAvail / contentW)

  let side: 'left' | 'right'
  let scale: number

  if (rightScale >= minScale && rightScale >= leftScale) {
    side = 'right'
    scale = rightScale
  } else if (leftScale >= minScale) {
    side = 'left'
    scale = leftScale
  } else {
    return null
  }

  const contentTopPx = contentTop * spriteH0 * scale
  const contentBottomPx = contentBottom * spriteH0 * scale

  const x =
    side === 'right'
      ? geom.x + geom.w + margin - contentLeft * spriteW * scale
      : geom.x - margin - contentRight * spriteW * scale

  const y = Math.max(
    -contentTopPx,
    Math.min(geom.y + geom.h - margin - contentBottomPx, viewport.height - REST_MARGIN - contentBottomPx)
  )

  return { pos: { x, y }, scale }
}

// 弹层按实际露出范围定位；右侧放不下时翻到左侧，并限制在视口内。
export function computeOverlayAnchorBesideSprite(opts: {
  pos: { x: number; y: number }
  scale: number
  anchorRect: SpriteRect | null
  peek: SpatialPeek | null
  gap: number
  overlayW: number
  overlayH?: number
  vw: number
  vh: number
  verticalRatio?: number
}): { left: number; top: number; side: 'left' | 'right' } {
  const { pos, scale, anchorRect, peek, gap, overlayW, overlayH = 0, vw, vh, verticalRatio = 0 } = opts
  const base = baseSpriteSize(vh)
  const width = base.width * scale
  const height = base.height * scale
  let visibleLeft = pos.x + (anchorRect?.left ?? 0) * width
  let visibleRight = pos.x + (anchorRect?.right ?? 1) * width
  let visibleTop = pos.y + (anchorRect?.top ?? 0) * height
  let visibleBottom = pos.y + (anchorRect?.bottom ?? 1) * height

  if (peek) {
    const [left, top, right, bottom] = peek.focusRect
    visibleLeft = Math.max(visibleLeft, pos.x + left * width)
    visibleRight = Math.min(visibleRight, pos.x + right * width)
    visibleTop = Math.max(visibleTop, pos.y + top * height)
    visibleBottom = Math.min(visibleBottom, pos.y + bottom * height)

    if (visibleLeft >= visibleRight || visibleTop >= visibleBottom) {
      visibleLeft = pos.x + left * width
      visibleRight = pos.x + right * width
      visibleTop = pos.y + top * height
      visibleBottom = pos.y + bottom * height
    }
  }

  if (peek?.mode === 'screen') {
    const cut = pos.x + peek.cutX * width

    if (peek.side === 'left') {
      visibleRight = Math.min(visibleRight, cut)
    } else {
      visibleLeft = Math.max(visibleLeft, cut)
    }
  }

  const rightSpace = vw - visibleRight - gap
  const leftSpace = visibleLeft - gap
  const side = rightSpace >= overlayW || rightSpace >= leftSpace ? 'right' : 'left'

  const left = clamp(
    side === 'right' ? visibleRight + gap : visibleLeft - gap - overlayW,
    0,
    Math.max(0, vw - overlayW)
  )

  const top = clamp(visibleTop + (visibleBottom - visibleTop) * verticalRatio, 0, Math.max(0, vh - overlayH))

  return { left, top, side }
}

export function generateRoamWaypoint(
  viewport: ViewportSize,
  scale: number,
  random: () => number = Math.random
): Position {
  const { width, height } = baseSpriteSize(viewport.height)
  const w = width * scale
  const h = height * scale

  return {
    x: REST_MARGIN + random() * Math.max(0, viewport.width - w - 2 * REST_MARGIN),
    y: Math.max(REST_MARGIN, viewport.height * 0.5 + random() * Math.max(0, viewport.height * 0.4 - h))
  }
}

export function dragScreenEdge(
  position: Position,
  viewport: ViewportSize,
  content: SpriteRect,
  scale: number,
  cancelled: boolean
): 'left' | 'right' | null {
  const box = spriteContentBox(viewport, content, scale)
  const width = Math.max(1, box.right - box.left)
  const leftClipped = Math.max(0, -(position.x + box.left))
  const rightClipped = Math.max(0, position.x + box.right - viewport.width)

  return !cancelled && rightClipped / width >= 0.25
    ? 'right'
    : !cancelled && leftClipped / width >= 0.25
      ? 'left'
      : null
}
