// 以独立同源脚本加载，生产 CSP 无需放开 blob: 或内联脚本。
declare const sampleRate: number
declare class AudioWorkletProcessor {
  port: MessagePort
}
declare function registerProcessor(name: string, processor: new () => AudioWorkletProcessor): void

class CallInputProcessor extends AudioWorkletProcessor {
  private frame = new Float32Array(480)
  private used = 0
  private phase = 0
  private total = 0
  private count = 0
  process(inputs: Float32Array[][]): boolean {
    const samples = inputs[0]?.[0]

    if (!samples) {
      return true
    }

    for (const sample of samples) {
      this.total += sample
      this.count++
      this.phase += 24000

      while (this.phase >= sampleRate) {
        this.phase -= sampleRate
        this.frame[this.used++] = this.count ? this.total / this.count : sample
        this.total = 0
        this.count = 0

        if (this.used === this.frame.length) {
          this.port.postMessage(this.frame.buffer, [this.frame.buffer])
          this.frame = new Float32Array(480)
          this.used = 0
        }
      }
    }

    return true
  }
}

interface OutputFrame {
  samples: Float32Array
  sequence: number
  turnSeq: number
  playbackEpoch: number
}

class CallOutputProcessor extends AudioWorkletProcessor {
  private queue: OutputFrame[] = []
  private offset = 0
  private length = 0
  constructor() {
    super()

    this.port.onmessage = (
      event: MessageEvent<{
        type: string
        samples: ArrayBuffer
        sequence: number
        turnSeq: number
        playbackEpoch: number
      }>
    ) => {
      if (event.data.type === 'clear') {
        this.queue = []
        this.offset = 0
        this.length = 0

        return
      }

      const input = new Float32Array(event.data.samples)
      const samples = new Float32Array(Math.round((input.length * sampleRate) / 24000))

      for (let i = 0; i < samples.length; i++) {
        const position = (i * 24000) / sampleRate

        const left = Math.floor(position),
          fraction = position - left

        samples[i] = (input[left] ?? 0) * (1 - fraction) + (input[Math.min(left + 1, input.length - 1)] ?? 0) * fraction
      }

      if (this.length + samples.length > sampleRate * 2) {
        this.queue = []
        this.offset = 0
        this.length = 0
        this.port.postMessage({ type: 'overflow' })

        return
      }

      this.length += samples.length
      this.queue.push({
        samples,
        sequence: event.data.sequence,
        turnSeq: event.data.turnSeq,
        playbackEpoch: event.data.playbackEpoch
      })
    }
  }
  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const output = outputs[0]?.[0]

    if (!output) {
      return true
    }

    output.fill(0)
    let written = 0

    while (written < output.length && this.queue.length) {
      const frame = this.queue[0]
      const size = Math.min(output.length - written, frame.samples.length - this.offset)
      output.set(frame.samples.subarray(this.offset, this.offset + size), written)
      written += size
      this.offset += size
      this.length -= size

      if (this.offset === frame.samples.length) {
        this.queue.shift()
        this.offset = 0
        this.port.postMessage({
          type: 'played',
          sequence: frame.sequence,
          turnSeq: frame.turnSeq,
          playbackEpoch: frame.playbackEpoch
        })
      }
    }

    return true
  }
}
registerProcessor('spiritagent-input', CallInputProcessor)
registerProcessor('spiritagent-output', CallOutputProcessor)
