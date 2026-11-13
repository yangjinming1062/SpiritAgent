import type { CallAudioFrameMeta, RealtimeCallController } from './realtime-call'
import workletUrl from './realtime-worklet.ts?worker&url'

const MAX_AUDIO_FRAME_BYTES = 9600

export interface CallLevels {
  microphone: number
  playback: number
}

function pcm16(samples: Float32Array): ArrayBuffer {
  const bytes = new ArrayBuffer(samples.length * 2),
    view = new DataView(bytes)

  for (let i = 0; i < samples.length; i++) {
    view.setInt16(i * 2, Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767), true)
  }

  return bytes
}

/** 单一通话的音频设备，异步授权完成前结束也会回收迟到的音轨。 */
export class RealtimeAudioBridge {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private input: AudioWorkletNode | null = null
  private output: AudioWorkletNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private sink: GainNode | null = null
  private revision = 0
  private analyser: AnalyserNode | null = null
  private meterSamples = new Float32Array(256)
  private microphone = 0
  private started = false
  private speaking = false
  private voicedMs = 0
  private quietMs = 0
  private speakingMs = 0
  private noiseFloor = 0.008
  private preRoll: Float32Array[] = []
  private acknowledgements = new Set<ReturnType<typeof setTimeout>>()
  constructor(private readonly controller: RealtimeCallController) {}
  async start(): Promise<void> {
    const revision = ++this.revision
    const context = new AudioContext()
    this.context = context

    try {
      await context.resume()
      if (revision !== this.revision) {
        return
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      })

      if (revision !== this.revision) {
        stream.getTracks().forEach(track => track.stop())

        return
      }

      this.stream = stream

      for (const track of stream.getTracks()) {
        track.addEventListener('ended', () => this.controller.fail('麦克风已断开'), { once: true })
      }

      await context.audioWorklet.addModule(workletUrl)

      if (revision !== this.revision) {
        return
      }

      this.source = context.createMediaStreamSource(stream)
      this.input = new AudioWorkletNode(context, 'spiritagent-input')
      this.output = new AudioWorkletNode(context, 'spiritagent-output', {
        outputChannelCount: [1]
      })
      this.input.port.onmessage = (event: MessageEvent<ArrayBuffer>) => this.capture(new Float32Array(event.data))

      this.output.port.onmessage = (event: MessageEvent<CallAudioFrameMeta & { type: string }>) => {
        if (event.data.type === 'overflow') {
          this.controller.fail('音频播放积压，通话已结束')

          return
        }

        // worklet 回执在渲染块结束时产生，再等输出设备排队时延。
        const timer = setTimeout(
          () => {
            this.acknowledgements.delete(timer)

            if (revision === this.revision) {
              this.controller.played(event.data)
            }
          },
          Math.max(0, (context.baseLatency + (context.outputLatency || 0)) * 1000) + 10
        )

        this.acknowledgements.add(timer)
      }

      this.input.addEventListener('processorerror', () => this.controller.fail('音频采集失败'))
      this.output.addEventListener('processorerror', () => this.controller.fail('音频播放失败'))
      this.source.connect(this.input)
      // 采集节点需要接入渲染图才能持续运行，但不能把麦克风送回扬声器。
      this.sink = context.createGain()
      this.sink.gain.value = 0
      this.input.connect(this.sink)
      this.sink.connect(context.destination)
      this.analyser = context.createAnalyser()
      this.analyser.fftSize = this.meterSamples.length
      this.output.connect(this.analyser)
      this.analyser.connect(context.destination)
      this.started = true
    } catch (error) {
      await this.stop()
      throw error
    }
  }
  enqueueAudio(frame: ArrayBuffer, meta: CallAudioFrameMeta): void {
    if (!this.output || !this.controller.current(meta)) {
      return
    }

    const pcm = new DataView(frame),
      samples = new Float32Array(frame.byteLength / 2)

    for (let i = 0; i < samples.length; i++) {
      samples[i] = pcm.getInt16(i * 2, true) / 32768
    }

    this.output.port.postMessage({ type: 'audio', ...meta, samples: samples.buffer }, [samples.buffer])
  }
  levels(): CallLevels {
    let energy = 0
    if (this.analyser) {
      this.analyser.getFloatTimeDomainData(this.meterSamples)
      for (const value of this.meterSamples) {
        energy += value * value
      }
    }

    const state = this.controller.snapshot().state

    return {
      microphone: this.microphone,
      playback: state === 'speaking' ? Math.sqrt(energy / this.meterSamples.length) : 0
    }
  }
  clearOutput(): void {
    this.output?.port.postMessage({ type: 'clear' })
    this.acknowledgements.forEach(timer => clearTimeout(timer))
    this.acknowledgements.clear()
  }
  mute(muted: boolean): void {
    this.stream?.getAudioTracks().forEach(track => {
      track.enabled = !muted
    })
    this.speaking = false
    this.preRoll = []
    this.voicedMs = 0
    this.quietMs = 0
    this.noiseFloor = 0.008
    this.microphone = 0
    this.controller.mute(muted)
  }
  async stop(): Promise<void> {
    ++this.revision
    this.started = false
    this.clearOutput()
    this.input?.port.close()
    this.output?.port.close()
    this.source?.disconnect()
    this.input?.disconnect()
    this.output?.disconnect()
    this.sink?.disconnect()
    this.analyser?.disconnect()
    this.analyser = null
    this.microphone = 0
    this.stream?.getTracks().forEach(track => track.stop())
    this.stream = null
    this.source = null
    this.input = null
    this.output = null
    this.sink = null
    const context = this.context
    this.context = null

    if (context && context.state !== 'closed') {
      await context.close()
    }

    this.preRoll = []
    this.speaking = false
    this.noiseFloor = 0.008
  }
  private capture(samples: Float32Array): void {
    if (!this.started || this.controller.muted || ['connecting', 'ending'].includes(this.controller.snapshot().state)) {
      return
    }

    if (samples.length === 0) {
      return
    }

    let energy = 0

    for (const value of samples) {
      energy += value * value
    }

    const level = Math.sqrt(energy / samples.length)
    this.microphone = level
    const threshold = Math.min(0.05, Math.max(0.018, this.noiseFloor * 2.8))
    const voiced = level >= (this.speaking ? Math.max(0.012, threshold * 0.65) : threshold)

    if (!this.speaking) {
      if (!voiced) {
        // 只在门控关闭时跟踪本机噪声底：阈值下限挡住键盘声与低回声，自适应部分适应安静房间。
        this.noiseFloor = Math.min(0.04, this.noiseFloor * 0.96 + level * 0.04)
      }

      this.preRoll.push(samples)

      if (this.preRoll.length > 15) {
        this.preRoll.shift()
      }

      this.voicedMs = voiced ? this.voicedMs + 20 : 0

      if (this.voicedMs < 160) {
        return
      }

      this.speaking = true
      this.speakingMs = 0
      this.quietMs = 0
      this.controller.beginInputTurn()

      // 把预录样本合并成协议允许的较大帧，避免刚开口就耗尽发送队列。
      const preRoll = new Float32Array(this.preRoll.reduce((length, frame) => length + frame.length, 0))
      let offset = 0

      for (const frame of this.preRoll) {
        preRoll.set(frame, offset)
        offset += frame.length
      }

      const pcm = pcm16(preRoll)

      for (let start = 0; start < pcm.byteLength; start += MAX_AUDIO_FRAME_BYTES) {
        this.controller.sendAudio(pcm.slice(start, start + MAX_AUDIO_FRAME_BYTES))
      }

      this.preRoll = []

      return
    }

    this.controller.sendAudio(pcm16(samples))
    this.speakingMs += 20
    this.quietMs = voiced ? 0 : this.quietMs + 20

    if (this.quietMs >= 800 || this.speakingMs >= 55000) {
      this.speaking = false
      this.voicedMs = 0
      this.controller.endInputTurn()
    }
  }
}
