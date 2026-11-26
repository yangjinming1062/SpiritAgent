import { log } from '@/shared/lib/log'
import { gatewayApi, mediaApi } from '@/shared/platform/desktop'

import { $callAudioBusy } from './call-audio-focus'

export type AudioPlaybackResult = 'completed' | 'interrupted' | 'failed'

export interface AudioPlaybackOptions {
  /** 取消仅结束本次播放，不停止已接管的其他声音。 */
  signal?: AbortSignal
  startAtSeconds?: number
  onStarted?: () => void
  onProgress?: (positionSeconds: number, completed: boolean, flush: boolean) => void
}

let currentDone: (() => void) | null = null
let playGen = 0

export function stopAudio(): void {
  playGen++

  currentDone?.()
}

export function nextGen(): number {
  return ++playGen
}

export function isLatestGen(gen: number): boolean {
  return gen === playGen
}

export async function playDataUrl(dataUrl: string, options: AudioPlaybackOptions = {}): Promise<AudioPlaybackResult> {
  if ($callAudioBusy.get() || options.signal?.aborted) {
    return 'interrupted'
  }

  // 先登记请求，迟到的通话占用回包不能让已取消的播放接管音频。
  const gen = nextGen()
  let callBusy = false

  try {
    callBusy = await gatewayApi().voiceBusy()
  } catch {
    callBusy = false
  }

  if (!isLatestGen(gen) || $callAudioBusy.get() || options.signal?.aborted || callBusy) {
    return 'interrupted'
  }

  currentDone?.()
  const audio = new Audio()
  const listeners = new AbortController()
  const { signal } = listeners
  let started = false
  let resolvePlayback!: (result: AudioPlaybackResult) => void

  const playbackEnded = new Promise<AudioPlaybackResult>(resolve => {
    resolvePlayback = resolve
  })

  let fired = false
  let preparationTimer: ReturnType<typeof setTimeout> | undefined

  const fireDone = (result: AudioPlaybackResult): void => {
    if (fired) {
      return
    }

    fired = true
    clearTimeout(preparationTimer)
    options.signal?.removeEventListener('abort', stopDone)

    // 先结算进度，再释放 src；load() 会把 currentTime 重置为零。
    try {
      if (started || result === 'completed') {
        options.onProgress?.(audio.currentTime, result === 'completed', true)
      }
    } catch (error) {
      log.warn('audio-track', 'Could not report final playback progress', error)
    }

    listeners.abort()

    if (currentDone === stopDone) {
      currentDone = null
    }

    audio.pause()
    audio.removeAttribute('src')
    audio.load()
    resolvePlayback(result)
  }

  // 主动停止与其他声音抢占都属于中断，不能作为音频损坏上报。
  const stopDone = (): void => fireDone('interrupted')

  currentDone = stopDone
  options.signal?.addEventListener('abort', stopDone, { once: true })

  const releaseCallListener = $callAudioBusy.listen(busy => {
    if (busy) {
      stopDone()
    }
  })

  void playbackEnded.then(releaseCallListener)

  audio.addEventListener('ended', () => fireDone('completed'), { signal })
  audio.addEventListener(
    'error',
    () => {
      log.warn('audio-track', 'Media element failed', audio.error?.code)
      fireDone('failed')
    },
    { signal }
  )
  audio.addEventListener(
    'timeupdate',
    () => {
      if (started && !fired) {
        options.onProgress?.(audio.currentTime, false, false)
      }
    },
    { signal }
  )

  const metadata = new Promise<void>(resolve => {
    audio.addEventListener('loadedmetadata', () => resolve(), { once: true, signal })

    if (audio.readyState >= 1) {
      resolve()
    }
  })

  preparationTimer = setTimeout(() => {
    log.warn('audio-track', 'Playback preparation timed out')
    fireDone('failed')
  }, 30_000)
  audio.src = dataUrl

  const alive = (): boolean => !fired && currentDone === stopDone

  const prepare = async (): Promise<void> => {
    // 输出由主进程在启动时准备并持续保持；各窗口只共享就绪结果，不重复播放预热片段。
    await Promise.race([Promise.all([metadata, mediaApi().media.prepareOutput()]), playbackEnded])

    if (!alive()) {
      return
    }

    // 失效断点从头恢复，避免直接 seek 到结尾被误判为已听。
    const position = options.startAtSeconds ?? 0

    if (Number.isFinite(position) && position > 0 && position < audio.duration) {
      audio.currentTime = position
    }

    await audio.play()

    if (alive()) {
      started = true
      clearTimeout(preparationTimer)
      options.onStarted?.()
    }
  }

  // 中断须立即返回，即使浏览器尚在等待媒体元数据或开始播放。
  void prepare().catch(error => {
    if (!fired) {
      log.warn('audio-track', 'Playback failed', error)
      fireDone('failed')
    }
  })

  return await playbackEnded
}
