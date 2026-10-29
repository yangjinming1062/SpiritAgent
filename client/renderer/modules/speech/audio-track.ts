import { log } from '@/shared/lib/log'
import { mediaApi } from '@/shared/platform/desktop'

export type AudioPlaybackResult = 'completed' | 'interrupted' | 'failed'

export interface AudioPlaybackOptions {
  /** 取消仅结束本次播放，不停止已接管的其他声音。 */
  signal?: AbortSignal
  startAtSeconds?: number
  onStarted?: () => void
  onProgress?: (positionSeconds: number, completed: boolean, flush: boolean) => void
}

let current: HTMLAudioElement | null = null
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
  if (options.signal?.aborted) {
    return 'interrupted'
  }

  stopAudio()
  const gen = nextGen()
  const audio = new Audio(dataUrl)
  const listeners = new AbortController()
  const { signal } = listeners
  current = audio
  let position = Math.max(0, options.startAtSeconds ?? 0)
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

    if (started) {
      position = audio.currentTime
    }

    // 先结算进度，再释放 src；load() 会把 currentTime 重置为零。
    try {
      options.onProgress?.(position, result === 'completed', true)
    } catch (error) {
      log.warn('audio-track', 'Could not report final playback progress', error)
    }

    listeners.abort()

    if (currentDone === stopDone) {
      currentDone = null
    }

    if (current === audio) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
      current = null
    }

    resolvePlayback(result)
  }

  // 主动停止与其他声音抢占都属于中断，不能作为音频损坏上报。
  const stopDone = (): void => fireDone('interrupted')

  currentDone = stopDone
  options.signal?.addEventListener('abort', stopDone, { once: true })

  audio.addEventListener('ended', () => fireDone('completed'), { signal })
  audio.addEventListener('error', () => fireDone('failed'), { signal })
  audio.addEventListener(
    'timeupdate',
    () => {
      if (started && !fired) {
        position = audio.currentTime
        options.onProgress?.(position, false, false)
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

  preparationTimer = setTimeout(() => fireDone('failed'), 30000)

  // 本次播放仍有效：未结算、未被新代次抢占、仍持有播放元素。
  const alive = (): boolean => !fired && isLatestGen(gen) && current === audio

  const prepare = async (): Promise<void> => {
    // 输出由主进程在启动时准备并持续保持；各窗口只共享就绪结果，不重复播放预热片段。
    await Promise.race([Promise.all([metadata, mediaApi().media.prepareOutput()]), playbackEnded])

    if (!alive()) {
      return
    }

    // 失效断点从头恢复，避免直接 seek 到结尾被误判为已听。
    position = Number.isFinite(position) && position < audio.duration ? position : 0
    audio.currentTime = position
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
