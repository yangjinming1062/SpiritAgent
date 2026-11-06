import type { DesktopBackground } from '@ipc/contracts'

export interface TransitionLayer {
  capture: (element: HTMLVideoElement, media: DesktopBackground | null) => void
  fade: () => void
  reset: () => void
}

/** 换场遮罩：把上一帧画到画布再淡出，避免双层切换闪烁。 */
export function createTransitionLayer(
  canvas: HTMLCanvasElement,
  filterFor: (media: DesktopBackground | null) => string
): TransitionLayer {
  let fadeTimer: ReturnType<typeof setTimeout> | null = null
  let fadeFrame: number | null = null

  const stop = (): void => {
    if (fadeTimer) {
      clearTimeout(fadeTimer)
      fadeTimer = null
    }

    if (fadeFrame !== null) {
      cancelAnimationFrame(fadeFrame)
      fadeFrame = null
    }
  }

  const capture = (element: HTMLVideoElement, media: DesktopBackground | null): void => {
    const context = canvas.getContext('2d')

    if (!context) {
      return
    }

    stop()
    canvas.width = window.innerWidth
    canvas.height = window.innerHeight
    const fit = element.style.objectFit

    const scale = (fit === 'cover' ? Math.max : Math.min)(
      canvas.width / element.videoWidth,
      canvas.height / element.videoHeight
    )

    const width = element.videoWidth * scale
    const height = element.videoHeight * scale
    context.clearRect(0, 0, canvas.width, canvas.height)
    context.drawImage(element, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height)
    canvas.style.filter = filterFor(media)
    canvas.style.transition = 'none'
    canvas.style.opacity = '1'
  }

  const fade = (): void => {
    fadeFrame = requestAnimationFrame(() => {
      fadeFrame = requestAnimationFrame(() => {
        fadeFrame = null
        canvas.style.transition = 'opacity 180ms ease'
        canvas.style.opacity = '0'
        fadeTimer = setTimeout(() => {
          fadeTimer = null
        }, 180)
      })
    })
  }

  return {
    capture,
    fade,
    reset: () => {
      stop()
      canvas.style.opacity = '0'
    }
  }
}
