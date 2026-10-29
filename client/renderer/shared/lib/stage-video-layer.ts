import type { StageVideoLease } from './stage-video-cache'

export interface StageVideoSource<T> {
  key: string
  mediaKey?: string
  loop: boolean
  natural?: boolean
  value: T
  load: () => Promise<StageVideoLease>
}

interface LayerEvents<T> {
  presented: (source: StageVideoSource<T> | null, element: HTMLVideoElement | null) => void
  started: (source: StageVideoSource<T>) => void
  ended: (source: StageVideoSource<T>) => void
  failed: (source: StageVideoSource<T>, error: string) => void
  interrupted?: (source: StageVideoSource<T>) => void
  frame?: (element: HTMLVideoElement) => void
}

interface Slot<T> {
  element: HTMLVideoElement
  source: StageVideoSource<T> | null
  lease: StageVideoLease | null
  ready: boolean
  started: boolean
  terminal: boolean
  generation: number
  timeout: ReturnType<typeof setTimeout> | null
  frame: number | null
  mediaTime: number
}

/** 透明主体与环境共用的双缓冲层；新首帧就绪前保留旧画面，短动作仅回到仍有效的循环。 */
export function createStageVideoLayer<T>(elements: HTMLVideoElement[], events: LayerEvents<T>) {
  const slots: Slot<T>[] = elements.map(element => ({
    element,
    source: null,
    lease: null,
    ready: false,
    started: false,
    terminal: false,
    generation: 0,
    timeout: null,
    frame: null,
    mediaTime: 0
  }))

  let active: Slot<T> | null = null
  let selected: string | null = null
  let fallbackKey: string | null = null
  let paused = true
  let disposed = false
  const settled = new Set<string>()
  const failedMedia = new Set<string>()

  function settle(key: string): void {
    settled.add(key)
    const oldest = settled.values().next().value

    if (settled.size > 64 && oldest) {
      settled.delete(oldest)
    }
  }

  function cancelFrame(slot: Slot<T>): void {
    if (slot.frame !== null) {
      slot.element.cancelVideoFrameCallback(slot.frame)
    }

    slot.frame = null
  }

  function reset(slot: Slot<T>): void {
    slot.generation += 1
    cancelFrame(slot)

    if (slot.timeout) {
      clearTimeout(slot.timeout)
    }

    slot.timeout = null
    slot.element.pause()
    slot.element.style.opacity = '0'
    slot.element.removeAttribute('src')
    slot.element.load()
    slot.lease?.release()
    slot.lease = null
    slot.source = null
    slot.ready = false
    slot.started = false
    slot.terminal = false
    slot.mediaTime = 0
  }

  function watchFrame(slot: Slot<T>): void {
    cancelFrame(slot)

    if (paused || active !== slot || slot.terminal) {
      return
    }

    const generation = slot.generation
    slot.frame = slot.element.requestVideoFrameCallback((_now, metadata) => {
      slot.frame = null

      if (disposed || slot.generation !== generation || active !== slot) {
        return
      }

      const boundary = metadata.mediaTime < slot.mediaTime
      slot.mediaTime = metadata.mediaTime
      const waiting = slots.find(other => other.ready && other.source?.key === selected && other !== slot)

      if (boundary && waiting && slot.source?.natural && waiting.source?.natural) {
        show(waiting)

        return
      }

      events.frame?.(slot.element)
      watchFrame(slot)
    })
  }

  function start(slot: Slot<T>): void {
    if (paused || !slot.ready || slot.terminal || active !== slot) {
      slot.element.pause()
      cancelFrame(slot)

      return
    }

    const generation = slot.generation
    void slot.element
      .play()
      .then(() => {
        if (disposed || slot.generation !== generation || active !== slot || paused || !slot.source) {
          return
        }

        if (!slot.started) {
          slot.started = true
          events.started(slot.source)
        }

        watchFrame(slot)
      })
      .catch(error => {
        if (!disposed && !paused && slot.generation === generation) {
          fail(slot, String(error))
        }
      })
  }

  function show(slot: Slot<T>): void {
    const previous = active
    active = slot
    slot.element.style.opacity = '1'

    for (const other of slots) {
      if (other === slot) {
        continue
      }

      other.element.style.opacity = '0'
      other.element.pause()
      cancelFrame(other)
    }

    events.presented(slot.source, slot.element)

    if (previous && previous !== slot && (previous.source?.mediaKey ?? previous.source?.key) !== fallbackKey) {
      if (!previous.terminal && previous.source) {
        events.interrupted?.(previous.source)
      }

      reset(previous)
    }

    start(slot)
  }

  function restore(slot: Slot<T>): void {
    const fallback = slots.find(
      other =>
        other !== slot &&
        other.ready &&
        other.source &&
        (other.source.mediaKey ?? other.source.key) === fallbackKey &&
        other.source.loop
    )

    if (active === slot) {
      active = null

      if (fallback) {
        show(fallback)
      } else {
        events.presented(null, null)
      }
    }

    reset(slot)
  }

  function fail(slot: Slot<T>, error: string): void {
    if (!slot.source || slot.terminal) {
      return
    }

    const source = slot.source
    settle(source.key)
    failedMedia.add(source.mediaKey ?? source.key)
    slot.terminal = true
    restore(slot)
    events.failed(source, error)
  }

  function ready(slot: Slot<T>): void {
    if (!slot.source || slot.ready || slot.terminal) {
      return
    }

    slot.ready = true

    if (slot.timeout) {
      clearTimeout(slot.timeout)
    }

    slot.timeout = null

    presentReady(slot)
  }

  function presentReady(slot: Slot<T>): void {
    if (
      slot.source?.key === selected &&
      !(active?.source?.natural && slot.source?.natural && !paused && active !== slot)
    ) {
      show(slot)
    }
  }

  const removers = slots.map(slot => {
    const loaded = (): void => {
      if (slot.ready || !slot.source || slot.frame !== null) {
        return
      }

      const generation = slot.generation
      slot.frame = slot.element.requestVideoFrameCallback(() => {
        slot.frame = null

        if (!disposed && slot.generation === generation) {
          ready(slot)
        }
      })
    }

    const ended = (): void => {
      if (active !== slot || !slot.source || slot.source.loop || slot.terminal || !slot.element.ended) {
        return
      }

      const source = slot.source
      settle(source.key)
      slot.terminal = true
      restore(slot)
      events.ended(source)
    }

    const failed = (): void => {
      if (slot.element.error) {
        fail(slot, slot.element.error.message || 'Video could not be decoded')
      }
    }

    slot.element.addEventListener('loadeddata', loaded)
    slot.element.addEventListener('error', failed)
    slot.element.addEventListener('ended', ended)

    return () => {
      slot.element.removeEventListener('loadeddata', loaded)
      slot.element.removeEventListener('error', failed)
      slot.element.removeEventListener('ended', ended)
    }
  })

  function setPaused(value: boolean): void {
    paused = value

    if (active) {
      start(active)
    }
  }

  return {
    setPaused,
    set(source: StageVideoSource<T> | null, baseKey: string | null = null): void {
      if (disposed) {
        return
      }

      selected = source?.key ?? null
      fallbackKey = baseKey

      if (!source) {
        active = null

        for (const slot of slots) {
          if (slot.source && !slot.terminal) {
            events.interrupted?.(slot.source)
          }
        }

        slots.forEach(reset)
        events.presented(null, null)

        return
      }

      // 已终态的任务不重装；无独立任务身份的基础媒体按版本抑制解码失败后的自动重试。
      const mediaKey = source.mediaKey ?? source.key

      if (settled.has(source.key) || (mediaKey === source.key && failedMedia.has(mediaKey))) {
        return
      }

      const existing = slots.find(
        slot =>
          slot.source?.key === source.key ||
          (source.mediaKey &&
            slot.source?.mediaKey === source.mediaKey &&
            slot.source.loop &&
            source.loop &&
            slot.ready)
      )

      if (existing) {
        if (existing.source?.key !== source.key) {
          if (!existing.terminal && existing.source) {
            events.interrupted?.(existing.source)
          }

          existing.started = false
        }

        existing.source = source

        if (existing.ready && !existing.terminal) {
          presentReady(existing)
        }

        return
      }

      const slot = slots.find(candidate => candidate !== active)

      if (!slot) {
        throw new Error('Stage video layer requires two buffers')
      }

      if (slot.source && !slot.terminal) {
        events.interrupted?.(slot.source)
      }

      reset(slot)
      slot.source = source
      const generation = slot.generation
      slot.timeout = setTimeout(() => fail(slot, 'Video first frame timed out'), 15000)
      void source
        .load()
        .then(lease => {
          if (disposed || slot.generation !== generation) {
            lease.release()

            return
          }

          slot.lease = lease
          slot.element.loop = source.loop
          slot.element.muted = true
          slot.element.src = lease.url
          slot.element.load()
          slot.frame = slot.element.requestVideoFrameCallback(() => {
            slot.frame = null

            if (!disposed && slot.generation === generation) {
              ready(slot)
            }
          })
        })
        .catch(error => {
          if (!disposed && slot.generation === generation) {
            fail(slot, String(error))
          }
        })
    },
    dispose(): void {
      disposed = true
      active = null
      removers.forEach(remove => remove())

      for (const slot of slots) {
        if (slot.source && !slot.terminal) {
          events.interrupted?.(slot.source)
        }
      }

      slots.forEach(reset)
    }
  }
}
