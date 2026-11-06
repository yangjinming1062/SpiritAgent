import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'
import type {
  DesktopBackground,
  DesktopBackgroundPlayback,
  DesktopStageLayer,
  DesktopStageLayout
} from '@ipc/contracts'

import type { ReceiptLog } from './receipt'

export interface VideoSlot {
  element: HTMLVideoElement
  media: DesktopBackground | null
  videoUrl: string | null
  posterUrl: string | null
  ready: boolean
  started: boolean
  terminal: boolean
  timeout: ReturnType<typeof setTimeout> | null
  frameCallback: number | null
  generation: number
}

interface LayerElements {
  actorContainer: HTMLDivElement
  actorPoster: HTMLImageElement
  scenePoster: HTMLImageElement
  transition: HTMLCanvasElement
}

interface SlotHandlers {
  isDisposed: () => boolean
  loaded: () => void
  playing: () => void
  resize: () => void
  ended: () => void
  failed: () => void
}

interface ResetOptions {
  slot: VideoSlot
  slots: VideoSlot[]
  isPending: () => boolean
  onPendingCleared: () => void
  currentFillUrl: () => string | null
  setFill: (url: string | null) => void
  posterFallback: () => string | null
}

export function createVideoSlots(elements: HTMLVideoElement[]): VideoSlot[] {
  return elements.map(element => ({
    element,
    media: null,
    videoUrl: null,
    posterUrl: null,
    ready: false,
    started: false,
    terminal: false,
    timeout: null,
    frameCallback: null,
    generation: 0
  }))
}

export function layerZIndex(layout: DesktopStageLayout, layer: DesktopStageLayer): string {
  const index = layout.layerOrder.indexOf(layer)

  return String(index >= 0 ? index + 1 : layout.layerOrder.length + 1)
}

export function applyLayerOrder(layout: DesktopStageLayout, elements: LayerElements): void {
  elements.actorContainer.style.zIndex = layerZIndex(layout, 'actor')
  elements.actorPoster.style.zIndex = layerZIndex(layout, 'actor')
  elements.scenePoster.style.zIndex = layerZIndex(layout, 'scene')
  elements.transition.style.zIndex = layerZIndex(layout, 'effects')
}

export function updateMediaLayout(
  element: HTMLImageElement | HTMLVideoElement,
  width: number,
  height: number,
  stageLayout: DesktopStageLayout
): void {
  const [safeLeft, safeTop, safeRight, safeBottom] = stageLayout.safeArea
  const areaWidth = window.innerWidth * (safeRight - safeLeft)
  const areaHeight = window.innerHeight * (safeBottom - safeTop)

  const computed = desktopMediaLayout(width, height, areaWidth, areaHeight)
  const areaLeft = window.innerWidth * safeLeft
  const areaTop = window.innerHeight * safeTop

  const layout =
    stageLayout.fit === 'cover'
      ? { objectFit: 'cover' as const, left: areaLeft, top: areaTop, width: areaWidth, height: areaHeight }
      : {
          ...computed,
          left: computed.left + areaLeft,
          top: computed.top + areaTop
        }

  // 完整显示时媒体元素仅占实际内容区域，周围由模糊底层填充。
  Object.assign(element.style, {
    left: `${layout.left}px`,
    top: `${layout.top}px`,
    width: `${layout.width}px`,
    height: `${layout.height}px`,
    objectFit: layout.objectFit
  })
}

export function updateVideoLayout(slot: VideoSlot, fallbackLayout: DesktopStageLayout): void {
  updateMediaLayout(slot.element, slot.element.videoWidth, slot.element.videoHeight, slot.media?.layout ?? fallbackLayout)
}

export function waitForFrame(slot: VideoSlot, isDisposed: () => boolean, ready: () => void): void {
  if (slot.frameCallback !== null) {
    slot.element.cancelVideoFrameCallback(slot.frameCallback)
  }

  const generation = slot.generation
  slot.frameCallback = slot.element.requestVideoFrameCallback(() => {
    slot.frameCallback = null

    if (slot.generation === generation && !isDisposed()) {
      ready()
    }
  })
}

export function resetSlot({ slot, slots, isPending, onPendingCleared, currentFillUrl, setFill, posterFallback }: ResetOptions): void {
  slot.generation += 1

  if (slot.frameCallback !== null) {
    slot.element.cancelVideoFrameCallback(slot.frameCallback)
    slot.frameCallback = null
  }

  if (isPending()) {
    onPendingCleared()
  }

  if (slot.timeout) {
    clearTimeout(slot.timeout)
  }

  slot.timeout = null
  slot.element.pause()
  slot.element.removeAttribute('src')
  slot.element.removeAttribute('poster')
  slot.element.load()
  slot.element.style.opacity = '0'

  if (slot.videoUrl) {
    URL.revokeObjectURL(slot.videoUrl)
  }

  if (slot.posterUrl) {
    const revokedPoster = slot.posterUrl

    const replacement = slots.find(candidate => candidate !== slot && candidate.posterUrl)?.posterUrl ?? posterFallback() ?? null

    if (currentFillUrl() === revokedPoster) {
      setFill(replacement)
    }

    URL.revokeObjectURL(revokedPoster)
  }

  slot.media = null
  slot.videoUrl = null
  slot.posterUrl = null
  slot.ready = false
  slot.started = false
  slot.terminal = false
}

export function acknowledgeSlot(
  slot: VideoSlot,
  status: DesktopBackgroundPlayback['status'],
  error: string | undefined,
  receipts: ReceiptLog
): void {
  const media = slot.media

  if (!media?.playId) {
    return
  }

  if (status === 'completed' || status === 'failed' || status === 'interrupted') {
    if (slot.terminal) {
      return
    }

    slot.terminal = true
  }

  receipts.send(media, status, error)
}

export function attachSlotListeners(slot: VideoSlot, handlers: SlotHandlers): () => void {
  const loaded = (): void => {
    if (slot.media && !slot.ready && !slot.terminal && slot.frameCallback === null) {
      waitForFrame(slot, handlers.isDisposed, handlers.loaded)
    }
  }

  const playing = (): void => handlers.playing()
  const resized = (): void => handlers.resize()
  const ended = (): void => handlers.ended()
  const failed = (): void => handlers.failed()

  slot.element.addEventListener('loadeddata', loaded)
  slot.element.addEventListener('playing', playing)
  slot.element.addEventListener('resize', resized)
  slot.element.addEventListener('ended', ended)
  slot.element.addEventListener('error', failed)

  return () => {
    slot.element.removeEventListener('loadeddata', loaded)
    slot.element.removeEventListener('playing', playing)
    slot.element.removeEventListener('resize', resized)
    slot.element.removeEventListener('ended', ended)
    slot.element.removeEventListener('error', failed)
  }
}
