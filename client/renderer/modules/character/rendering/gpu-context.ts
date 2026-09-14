const CONTEXT_RESTORE_TIMEOUT_MS = 5000

interface WebGLContextRecoveryHandlers {
  onLost: () => void
  onRestored: () => void
  onTimeout: () => void
}

export function observeWebGLContext(canvas: HTMLCanvasElement, handlers: WebGLContextRecoveryHandlers): () => void {
  let contextLost = false
  let restoreTimer: number | null = null
  let stopped = false

  const clearRestoreTimer = (): void => {
    if (restoreTimer !== null) {
      window.clearTimeout(restoreTimer)
      restoreTimer = null
    }
  }

  const onContextLost = (event: Event): void => {
    event.preventDefault()

    if (stopped || contextLost) {
      return
    }

    contextLost = true
    handlers.onLost()
    restoreTimer = window.setTimeout(() => {
      restoreTimer = null

      if (!stopped && contextLost) {
        handlers.onTimeout()
      }
    }, CONTEXT_RESTORE_TIMEOUT_MS)
  }

  const onContextRestored = (): void => {
    if (stopped || !contextLost) {
      return
    }

    contextLost = false
    clearRestoreTimer()
    handlers.onRestored()
  }

  canvas.addEventListener('webglcontextlost', onContextLost)
  canvas.addEventListener('webglcontextrestored', onContextRestored)

  return (): void => {
    if (stopped) {
      return
    }

    stopped = true
    clearRestoreTimer()
    canvas.removeEventListener('webglcontextlost', onContextLost)
    canvas.removeEventListener('webglcontextrestored', onContextRestored)
  }
}
