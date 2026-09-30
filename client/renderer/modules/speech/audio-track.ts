import { log } from '@/shared/lib/log'

export type AudioPlaybackResult = 'completed' | 'interrupted' | 'failed'

export interface AudioPlaybackOptions {
  startAtSeconds?: number
  onStarted?: () => void
  onProgress?: (positionSeconds: number, completed: boolean, flush: boolean) => void
}

let current: HTMLAudioElement | null = null
let currentDone: (() => void) | null = null
let playGen = 0

let audioCtx: AudioContext | null = null
let playbackSource: MediaElementAudioSourceNode | null = null

function disconnectPlaybackSource(): void {
  if (playbackSource) {
    try {
      playbackSource.disconnect()
    } catch {
      /* ignore */
    }

    playbackSource = null
  }
}

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

/** 预热 AudioContext 至 running 并保持不 suspend（否则切换语音条的 resume 与 MediaElementSource 重路由叠加会丢首帧）；挂起交由系统/浏览器处理。 */
export function warmAudioContext(): void {
  const ctx = ensureAudioContext()

  if (ctx.state === 'suspended') {
    void ctx.resume().catch(() => undefined)
  }
}

function ensureAudioContext(): AudioContext {
  audioCtx ??= new AudioContext()

  return audioCtx
}

/** 先恢复并接好 Web Audio 输出链，再启动媒体时间轴；否则冷启动重路由期间时间轴仍会前进，实际出声时已跳过开头。 */
async function connectPlaybackGraph(audio: HTMLAudioElement, gen: number): Promise<void> {
  if (!isLatestGen(gen) || current !== audio) {
    return
  }

  const ctx = ensureAudioContext()

  if (ctx.state === 'suspended') {
    await ctx.resume().catch(() => undefined)
  }

  if (!isLatestGen(gen) || current !== audio || ctx.state !== 'running') {
    return
  }

  // 每个 audio 元素创建一个 MediaElementSource。跨多次切换复用会泄漏图节点，并触发 "HTMLMediaElement already connected" 的 DOMException。
  try {
    disconnectPlaybackSource()
    playbackSource = ctx.createMediaElementSource(audio)
    playbackSource.connect(ctx.destination)
  } catch (err) {
    // 接图失败不阻断播放，记录原因便于排查无声。
    log.warn('audio-track', 'Playback graph connection failed:', err)

    return
  }

  if (!isLatestGen(gen) || current !== audio) {
    disconnectPlaybackSource()
  }
}

export async function playDataUrl(dataUrl: string, options: AudioPlaybackOptions = {}): Promise<AudioPlaybackResult> {
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
      disconnectPlaybackSource()
    }

    resolvePlayback(result)
  }

  // 主动停止与其他声音抢占都属于中断，不能作为音频损坏上报。
  const stopDone = (): void => fireDone('interrupted')

  currentDone = stopDone

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

  const prepare = async (): Promise<void> => {
    await Promise.race([metadata, playbackEnded])

    if (fired || !isLatestGen(gen) || current !== audio) {
      return
    }

    // 失效断点从头恢复，避免直接 seek 到结尾被误判为已听。
    position = Number.isFinite(position) && position < audio.duration ? position : 0
    audio.currentTime = position
    await connectPlaybackGraph(audio, gen)

    if (fired || !isLatestGen(gen) || current !== audio) {
      return
    }

    await audio.play()

    if (!fired && isLatestGen(gen) && current === audio) {
      started = true
      clearTimeout(preparationTimer)
      options.onStarted?.()
    }
  }

  // 中断须立即返回，即使浏览器尚在等待媒体元数据或 AudioContext.resume。
  void prepare().catch(error => {
    if (!fired) {
      log.warn('audio-track', 'Playback failed', error)
      fireDone('failed')
    }
  })

  return await playbackEnded
}
