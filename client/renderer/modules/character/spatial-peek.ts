import type { DesktopWindowSceneSnapshot } from '@ipc/contracts'
import { clamp } from '@runtime'

import type { ActionClipEntry, PeekGeometry } from './actions'

interface PeekPose {
  action: 'peek_left' | 'peek_right'
  side: 'left' | 'right'
  cutX: number
  focusRect: [number, number, number, number]
}

export type SpatialPeek =
  | (PeekPose & { mode: 'screen' })
  | (PeekPose & {
      mode: 'window'
      windowId: string
      windowPid: number
      runnerInstanceId: string
      targetRect: { x: number; y: number; w: number; h: number }
      occluders: Array<{ x: number; y: number; w: number; h: number }>
    })

export interface WindowPeekLayout {
  peek: Extract<SpatialPeek, { mode: 'window' }>
  position: { x: number; y: number }
  scale: number
}

export function computeScreenPeekLayout(
  edge: { side: 'left' | 'right'; yRatio: number },
  geometry: PeekGeometry,
  canvas: PeekRect,
  viewport: { width: number; height: number },
  stage: { width: number; height: number },
  scale: number
): { peek: Extract<SpatialPeek, { mode: 'screen' }>; position: { x: number; y: number } } {
  const width = canvas.right - canvas.left
  const height = canvas.bottom - canvas.top
  const cutX = canvas.left + geometry.cut_x * width

  const focusRect: [number, number, number, number] = [
    canvas.left + geometry.focus_rect[0] * width,
    canvas.top + geometry.focus_rect[1] * height,
    canvas.left + geometry.focus_rect[2] * width,
    canvas.top + geometry.focus_rect[3] * height
  ]

  const cutOffset = cutX * stage.width * scale

  return {
    peek: {
      mode: 'screen',
      action: edge.side === 'right' ? 'peek_left' : 'peek_right',
      side: geometry.side,
      cutX,
      focusRect
    },
    position: {
      x: (edge.side === 'right' ? viewport.width : 0) - cutOffset,
      y: clamp(
        edge.yRatio * viewport.height,
        -focusRect[1] * stage.height * scale,
        viewport.height - focusRect[3] * stage.height * scale
      )
    }
  }
}

export function computeWindowPeekLayout(
  scene: DesktopWindowSceneSnapshot,
  target: DesktopWindowSceneSnapshot['windows'][number],
  action: 'peek_left' | 'peek_right',
  clip: ActionClipEntry | undefined,
  canvas: PeekRect | null,
  stageW: number,
  stageH: number,
  maxScale: number,
  minScale: number
): WindowPeekLayout | null {
  const geometry = clip?.peek_geometry

  if (!clip || !geometry || !canvas || geometry.side !== (action === 'peek_left' ? 'left' : 'right')) {
    return null
  }

  const canvasW = canvas.right - canvas.left
  const canvasH = canvas.bottom - canvas.top
  const bounds = clip.content_rect ?? [0, 0, 1, 1]

  const body = {
    bottom: canvas.top + bounds[3] * canvasH,
    left: canvas.left + bounds[0] * canvasW,
    right: canvas.left + bounds[2] * canvasW,
    top: canvas.top + bounds[1] * canvasH
  }

  const cut = canvas.left + geometry.cut_x * canvasW
  const hiddenWidth = action === 'peek_right' ? cut - body.left : body.right - cut

  if (hiddenWidth <= 0 || body.bottom <= body.top) {
    return null
  }

  const rect = {
    h: target.h,
    w: target.w,
    x: target.x - scene.viewport.x,
    y: target.y - scene.viewport.y
  }

  const margin = 12

  const scale = Math.min(
    maxScale,
    (rect.h - 2 * margin) / ((body.bottom - body.top) * stageH),
    (rect.w - 2 * margin) / (hiddenWidth * stageW)
  )

  if (scale < minScale) {
    return null
  }

  const focusLeft = canvas.left + geometry.focus_rect[0] * canvasW
  const focusTop = canvas.top + geometry.focus_rect[1] * canvasH
  const focusRight = canvas.left + geometry.focus_rect[2] * canvasW
  const focusBottom = canvas.top + geometry.focus_rect[3] * canvasH
  const x = action === 'peek_right' ? rect.x + rect.w - cut * stageW * scale : rect.x - cut * stageW * scale
  const minY = rect.y + margin - body.top * stageH * scale
  const maxY = rect.y + rect.h - margin - body.bottom * stageH * scale

  if (minY > maxY) {
    return null
  }

  const y = clamp(rect.y + rect.h * 0.45 - ((focusTop + focusBottom) / 2) * stageH * scale, minY, maxY)

  const focusRect = {
    bottom: y + focusBottom * stageH * scale,
    left: x + focusLeft * stageW * scale,
    right: x + focusRight * stageW * scale,
    top: y + focusTop * stageH * scale
  }

  if (
    focusRect.left < 0 ||
    focusRect.right > scene.viewport.width ||
    focusRect.top < 0 ||
    focusRect.bottom > scene.viewport.height
  ) {
    return null
  }

  const occluders = scene.windows
    .filter(item => item.visible && item.zOrder < target.zOrder)
    .map(item => ({
      h: item.h,
      w: item.w,
      x: item.x - scene.viewport.x,
      y: item.y - scene.viewport.y
    }))

  return {
    peek: {
      action,
      cutX: cut,
      focusRect: [focusLeft, focusTop, focusRight, focusBottom],
      mode: 'window',
      occluders,
      runnerInstanceId: scene.runnerInstanceId,
      side: geometry.side,
      targetRect: rect,
      windowId: target.id,
      windowPid: target.pid
    },
    position: { x, y },
    scale
  }
}

export interface PeekRect {
  bottom: number
  left: number
  right: number
  top: number
}

export function peekMaskRects(
  peek: SpatialPeek | null,
  pos: { x: number; y: number },
  scale: number,
  width: number,
  height: number
): PeekRect[] {
  if (!peek) {
    return []
  }

  const rects: PeekRect[] = []

  const add = (left: number, top: number, right: number, bottom: number): void => {
    const clipped = {
      bottom: Math.min(height, bottom),
      left: Math.max(0, left),
      right: Math.min(width, right),
      top: Math.max(0, top)
    }

    if (clipped.left < clipped.right && clipped.top < clipped.bottom) {
      rects.push(clipped)
    }
  }

  if (peek.mode === 'screen') {
    const cut = peek.cutX * width
    add(peek.side === 'left' ? cut : 0, 0, peek.side === 'left' ? width : cut, height)

    return rects
  }

  const target = peek.targetRect

  const targetLeft = (target.x - pos.x) / scale
  const targetRight = (target.x + target.w - pos.x) / scale
  const targetTop = (target.y - pos.y) / scale
  const targetBottom = (target.y + target.h - pos.y) / scale
  add(peek.side === 'right' ? 0 : targetLeft, 0, peek.side === 'right' ? targetRight : width, height)
  add(0, 0, width, targetTop)
  add(0, targetBottom, width, height)

  for (const occluder of peek.occluders) {
    add(
      (occluder.x - pos.x) / scale,
      (occluder.y - pos.y) / scale,
      (occluder.x + occluder.w - pos.x) / scale,
      (occluder.y + occluder.h - pos.y) / scale
    )
  }

  return rects
}
