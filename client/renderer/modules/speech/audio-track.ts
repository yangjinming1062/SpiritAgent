import { log } from '@/shared/lib/log'

export type AudioPlaybackResult = 'completed' | 'interrupted' | 'failed'

let current: HTMLAudioElement | null = null
let currentDone: (() => void) | null = null
let currentListeners: [string, EventListener][] = []
let playGen = 0

let audioCtx: AudioContext | null = null
let playbackSource: MediaElementAudioSourceNode | null = null

function detachListeners(audio: HTMLAudioElement): void {
  for (const [type, fn] of currentListeners) {
    audio.removeEventListener(type, fn)
  }

  currentListeners = []
}

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

  if (current) {
    current.pause()
    // 释放 dataURL-backed src，让编码字节即使 ended/error 未触发也变得不可达。
    current.removeAttribute('src')
    current.load()
    detachListeners(current)
    current = null
  }

  if (currentDone) {
    currentDone()
    currentDone = null
  }

  disconnectPlaybackSource()
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

export async function playDataUrl(dataUrl: string, onDone?: () => void): Promise<AudioPlaybackResult> {
  stopAudio()
  const gen = nextGen()
  const audio = new Audio(dataUrl)
  current = audio

  // 在任何 await 之前就挂好 'ended' / 'error' 监听器，避免结束或出错事件抢在监听器挂好之前到达。
  let resolvePlayback!: (result: AudioPlaybackResult) => void

  const playbackEnded = new Promise<AudioPlaybackResult>(resolve => {
    resolvePlayback = resolve
  })

  // `fired` 让 `fireDone` 幂等：即使有多个来源（监听器、stopAudio、play-failure 分支）都试图结算这个 promise，只有第一次调用生效。
  let fired = false

  const fireDone = (result: AudioPlaybackResult): void => {
    if (fired) {
      return
    }

    fired = true

    if (currentDone === stopDone) {
      currentDone = null
    }

    disconnectPlaybackSource()

    resolvePlayback(result)

    if (onDone) {
      onDone()
    }
  }

  // 主动停止与其他声音抢占都属于中断，不能作为音频损坏上报。
  const stopDone = (): void => fireDone('interrupted')

  currentDone = stopDone

  const endedHandler: EventListener = () => fireDone('completed')
  const errorHandler: EventListener = () => fireDone('failed')
  audio.addEventListener('ended', endedHandler, { once: true })
  audio.addEventListener('error', errorHandler, { once: true })
  currentListeners = [
    ['ended', endedHandler],
    ['error', errorHandler]
  ]

  await connectPlaybackGraph(audio, gen)

  if (fired || !isLatestGen(gen) || current !== audio) {
    fireDone('interrupted')

    return await playbackEnded
  }

  const playResult = await audio.play().then(
    () => true,
    () => false
  )

  if (!playResult) {
    fireDone('failed')

    if (current === audio && isLatestGen(gen)) {
      stopAudio()
    }
  }

  return await playbackEnded
}
