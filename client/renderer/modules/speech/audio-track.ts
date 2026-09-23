import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

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
    // 释放 dataURL-backed src，让编码后的字节（最差约 256KB）即使在 ended/error
    // 没有触发的情况下也变得不可达。
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

/** 把模块 AudioContext 拉到 running——q1 冷启动时调用，避免 MediaElementSource 重路由吃掉首帧。
 *
 * AudioContext 进入 running 后保持运行；不再主动 `suspend()`，否则每次切换语音条
 * 都要 `await ctx.resume()`，与 MediaElementSource 重路由叠加会让首帧从 destination
 * 输出前被覆盖/丢弃。挂起改由系统/浏览器接管（`document.hidden` / 屏锁时 Chromium
 * 会自动挂起空闲 ctx），释放 WASAPI 定时器。 */
export function warmAudioContext(): void {
  ensureAudioContext()

  if (audioCtx && audioCtx.state === 'suspended') {
    void audioCtx.resume().catch(() => undefined)
  }
}

function ensureAudioContext(): void {
  if (audioCtx) {
    return
  }

  const Ctor = getAudioContextCtor()

  if (!Ctor) {
    return
  }

  audioCtx = new Ctor()
}

/** 先恢复并接好 Web Audio 输出链，再启动媒体时间轴；否则冷启动重路由期间
 *  时间轴仍会前进，实际出声时已跳过开头。 */
async function connectPlaybackGraph(audio: HTMLAudioElement, gen: number): Promise<void> {
  if (!isLatestGen(gen) || current !== audio) {
    return
  }

  ensureAudioContext()

  const ctx = audioCtx

  if (!ctx) {
    // 不支持 Web Audio——直接走 HTMLAudioElement 输出，不要崩。
    return
  }

  if (ctx.state === 'suspended') {
    await ctx.resume().catch(() => undefined)
  }

  if (!isLatestGen(gen) || current !== audio || ctx.state !== 'running') {
    return
  }

  // 每个 audio 元素创建一个 MediaElementSource。跨多次切换复用会泄漏图节点，
  // 并触发 "HTMLMediaElement already connected" 的 DOMException。
  try {
    disconnectPlaybackSource()
    playbackSource = ctx.createMediaElementSource(audio)
    playbackSource.connect(ctx.destination)
  } catch {
    // 该元素已经被连接（用全新的 Audio() 不应发生，但某些测试环境会复用节点）。
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

  // 在任何 await 之前就挂好 'ended' / 'error' 监听器，避免测试里的快速
  // `emit('ended')`（或真实的音频结束事件）抢在监听器挂好之前到达。
  let resolvePlayback!: (result: AudioPlaybackResult) => void

  const playbackEnded = new Promise<AudioPlaybackResult>(resolve => {
    resolvePlayback = resolve
  })

  // `fired` 让 `fireDone` 幂等：即使有多个来源（监听器、stopAudio、
  // play-failure 分支）都试图结算这个 promise，只有第一次调用生效。
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
