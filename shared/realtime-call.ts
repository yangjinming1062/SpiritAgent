export const REALTIME_CALL_VERSION = 2

export type CallState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'ending' | 'stopped' | 'error'
export interface PlaybackVersion {
  turnSeq: number
  playbackEpoch: number
}
export interface CallAudioFrameMeta extends PlaybackVersion {
  sequence: number
}
export interface CallPart {
  id: number
  text: string
  version: PlaybackVersion
}
export interface RealtimeCallSnapshot {
  state: CallState
  muted: boolean
  connectedAt: number | null
  turnSeq: number
  playbackEpoch: number
  error: string | null
  transcript: string
  assistant: string
  part: CallPart | null
}
export interface CallTransport {
  connect(): Promise<void>
  send(data: string | Uint8Array): Promise<void>
  close(): Promise<void>
}
export interface RealtimeCallControllerOptions {
  transport: CallTransport
  onSnapshot: (snapshot: RealtimeCallSnapshot) => void
  onAudioFrame: (frame: ArrayBuffer, meta: CallAudioFrameMeta) => void
  onClear: () => void
  onEnded: () => void
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const integer = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

/** 协议状态独立于采集/播放；每个二进制帧只消费它紧邻的控制头。 */
export class RealtimeCallController {
  private value: RealtimeCallSnapshot = {
    state: 'idle',
    muted: false,
    connectedAt: null,
    turnSeq: 0,
    playbackEpoch: 0,
    error: null,
    transcript: '',
    assistant: '',
    part: null
  }
  private pending: (CallAudioFrameMeta & { bytes: number }) | null = null
  private sequence = 0
  private heartbeat: ReturnType<typeof setInterval> | null = null
  private endingTimer: ReturnType<typeof setTimeout> | null = null
  private connectTimer: ReturnType<typeof setTimeout> | null = null
  private ready: (() => void) | null = null
  private rejectReady: ((error: Error) => void) | null = null
  private queued = 0
  private tail: Promise<void> = Promise.resolve()
  private closed = false
  private lastPong = performance.now()
  constructor(private readonly options: RealtimeCallControllerOptions) {}
  get muted(): boolean {
    return this.value.muted
  }
  snapshot(): RealtimeCallSnapshot {
    return { ...this.value }
  }
  private publish(): void {
    this.options.onSnapshot(this.snapshot())
  }
  current(version: PlaybackVersion): boolean {
    return !this.closed && version.turnSeq === this.value.turnSeq && version.playbackEpoch === this.value.playbackEpoch
  }
  async start(): Promise<void> {
    this.value.state = 'connecting'
    this.publish()

    const ready = new Promise<void>((resolve, reject) => {
      this.ready = resolve
      this.rejectReady = reject
    })

    void ready.catch(() => {})
    const timer = setTimeout(() => this.fail('通话连接超时'), 15000)

    try {
      await this.options.transport.connect()
      await ready

      if (this.closed) {
        return
      }

      this.heartbeat = setInterval(() => {
        if (this.value.state === 'connecting' || this.value.state === 'ending') {
          return
        }
        if (performance.now() - this.lastPong > 30000) {
          this.fail('通话连接已中断')

          return
        }

        this.send({ type: 'ping' })
      }, 10000)
    } catch (error) {
      if (!this.closed) {
        this.fail('无法建立通话，请重试')
      }

      throw error
    } finally {
      clearTimeout(timer)
      this.ready = null
      this.rejectReady = null
    }
  }
  private send(message: Record<string, unknown> | Uint8Array): void {
    if (this.closed) {
      return
    }

    if (++this.queued > 16) {
      this.fail('音频传输积压，通话已结束')

      return
    }

    const data = message instanceof Uint8Array ? message : JSON.stringify(message)
    this.tail = this.tail
      .then(async () => {
        if (!this.closed) {
          await this.options.transport.send(data)
        }
      })
      .catch(() => this.fail('通话连接已中断'))
      .finally(() => {
        this.queued--
      })
  }
  beginInputTurn(): void {
    if (this.closed || this.muted || this.value.state === 'connecting') {
      return
    }

    this.value.turnSeq++
    this.value.playbackEpoch++
    this.sequence = 0
    this.pending = null
    this.value.state = 'listening'
    this.value.error = null
    this.value.part = null
    this.value.transcript = ''
    this.value.assistant = ''
    this.options.onClear()
    this.send({
      type: 'input.start',
      turn_seq: this.value.turnSeq,
      playback_epoch: this.value.playbackEpoch
    })
    this.publish()
  }
  endInputTurn(): void {
    if (this.closed || this.muted) {
      return
    }

    this.send({
      type: 'input.end',
      turn_seq: this.value.turnSeq,
      playback_epoch: this.value.playbackEpoch
    })
    this.value.state = 'thinking'
    this.publish()
  }
  sendAudio(frame: ArrayBuffer): void {
    if (!this.muted && !this.closed) {
      this.send(new Uint8Array(frame))
    }
  }
  mute(muted: boolean): void {
    this.value.muted = muted
    this.send({ type: 'mute', muted })
    this.publish()
  }
  played(meta: CallAudioFrameMeta): void {
    if (this.current(meta)) {
      this.send({
        type: 'audio.played',
        sequence: meta.sequence,
        turn_seq: meta.turnSeq,
        playback_epoch: meta.playbackEpoch
      })
    }
  }
  receivedPart(part: CallPart): void {
    if (this.value.part === part && this.current(part.version)) {
      this.send({
        type: 'part.received',
        part_id: part.id,
        turn_seq: part.version.turnSeq,
        playback_epoch: part.version.playbackEpoch
      })
    }
  }
  receive(data: string | Uint8Array | ArrayBuffer): void {
    if (this.closed) {
      return
    }

    if (this.value.state === 'ending') {
      return
    }

    if (typeof data !== 'string') {
      const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : data
      const header = this.pending
      this.pending = null

      if (!header || !this.current(header)) {
        return
      }

      if (bytes.byteLength !== header.bytes) {
        this.fail('通话音频帧损坏')

        return
      }

      this.options.onAudioFrame(bytes.slice().buffer, header)

      return
    }

    let event: unknown

    try {
      event = JSON.parse(data)
    } catch {
      this.fail('通话响应格式错误')

      return
    }

    if (
      !record(event) ||
      typeof event.type !== 'string' ||
      !integer(event.turn_seq) ||
      !integer(event.playback_epoch)
    ) {
      this.fail('通话响应格式错误')

      return
    }

    const version = {
      turnSeq: event.turn_seq,
      playbackEpoch: event.playback_epoch
    }

    if (event.type === 'audio.chunk') {
      this.pending = null
    }

    // 心跳只属于连接本身；期间开始的输入回合不会让 pong 失效。
    if (event.type === 'pong') {
      this.lastPong = performance.now()
      return
    }

    if (!this.current(version)) {
      return
    }

    const payload = record(event.payload) ? event.payload : {}

    switch (event.type) {
      case 'session.ready':
        if (payload.version !== REALTIME_CALL_VERSION) {
          this.fail('通话版本不匹配，请更新应用后重试')

          return
        }

        this.lastPong = performance.now()
        this.ready?.()

        break

      case 'session.connected':
        if (this.connectTimer !== null) {
          clearTimeout(this.connectTimer)
          this.connectTimer = null
        }
        this.lastPong = performance.now()
        this.value.connectedAt = performance.now()
        this.value.state = 'listening'

        break

      case 'state':
        if (event.state === 'listening' || event.state === 'thinking' || event.state === 'speaking') {
          this.value.state = event.state
        }

        // 本地静音由用户控制，旧状态回包不能恢复麦克风。
        break

      case 'audio.chunk':
        if (
          payload.codec !== 'pcm_s16le' ||
          payload.sample_rate !== 24000 ||
          payload.channels !== 1 ||
          !integer(payload.byte_length) ||
          !payload.byte_length ||
          payload.byte_length > 9600 ||
          payload.byte_length % 2 ||
          !integer(payload.sequence) ||
          payload.sequence !== this.sequence + 1
        ) {
          this.fail('通话音频格式错误')

          return
        }

        this.sequence = payload.sequence
        this.pending = {
          ...version,
          sequence: payload.sequence,
          bytes: payload.byte_length
        }
        if (this.value.state === 'speaking' && this.value.part === null) {
          return
        }
        this.value.state = 'speaking'
        this.value.part = null

        break

      case 'transcript.partial':
        if (typeof payload.text === 'string') {
          this.value.transcript = payload.text
        }

        break

      case 'transcript.final':
        if (typeof payload.text === 'string') {
          this.value.transcript = payload.text
        }

        break

      case 'transcript.assistant':
        if (typeof payload.text === 'string') {
          this.value.assistant = payload.text
        }

        break

      case 'reply.part':
        if (integer(payload.part_id) && record(payload.bubble)) {
          const bubble = payload.bubble

          const text =
            typeof bubble.text === 'string'
              ? bubble.text
              : typeof bubble.title === 'string'
                ? bubble.title
                : bubble.type === 'image'
                  ? '图片已生成。'
                  : '已提交影像任务。'

          this.value.part = { id: payload.part_id, text, version }
        }

        break

      case 'error':
        this.value.error = typeof payload.message === 'string' ? payload.message : '本次通话处理失败'

        if (payload.fatal === true) {
          this.fail(this.value.error)

          return
        }

        break

      default:
        break
    }

    this.publish()
  }
  fail(message: string): void {
    this.finish(message)
  }
  private finish(message: string | null): void {
    if (this.closed) {
      return
    }

    this.closed = true
    if (this.connectTimer !== null) {
      clearTimeout(this.connectTimer)
      this.connectTimer = null
    }
    if (this.endingTimer !== null) {
      clearTimeout(this.endingTimer)
      this.endingTimer = null
    }
    this.pending = null

    if (this.heartbeat) {
      clearInterval(this.heartbeat)
      this.heartbeat = null
    }

    this.rejectReady?.(new Error(message ?? 'Call ended'))
    this.value.state = message ? 'error' : 'stopped'
    this.value.error = message
    this.options.onClear()
    this.publish()
    this.options.onEnded()
    void this.options.transport.close().catch(() => {})
  }
  audioReady(): void {
    if (this.closed || this.value.state !== 'connecting' || this.connectTimer !== null) {
      return
    }
    this.connectTimer = setTimeout(() => this.fail('通话连接超时'), 15000)
    this.send({ type: 'client.ready' })
  }
  stop(graceful = false): void {
    if (graceful && !this.closed && this.value.connectedAt !== null) {
      if (this.value.state === 'ending') {
        return
      }

      this.send({ type: 'stop' })
      if (this.closed) {
        return
      }
      this.value.state = 'ending'
      this.value.part = null
      this.options.onClear()
      this.publish()
      this.endingTimer = setTimeout(() => this.finish(null), 5000)

      return
    }

    this.finish(null)
  }
  transportClosed(): void {
    if (this.value.state === 'ending') {
      this.finish(null)
    } else {
      this.fail('通话连接已中断')
    }
  }
}
