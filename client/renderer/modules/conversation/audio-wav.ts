import { clamp } from '@runtime'

import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

export async function convertBlobToWav(blob: Blob, targetSampleRate = 16000): Promise<Blob> {
  const Ctor = getAudioContextCtor()

  if (!Ctor) {
    throw new Error('AudioContext is not supported in this environment')
  }

  const arrayBuffer = await blob.arrayBuffer()
  let ctx: AudioContext

  try {
    ctx = new Ctor({ sampleRate: targetSampleRate })
  } catch {
    ctx = new Ctor()
  }

  try {
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer)

    return encodeAudioBufferToWav(audioBuffer, targetSampleRate)
  } finally {
    void ctx.close().catch(() => {})
  }
}

/**
 * 将 AudioBuffer 转换为 16-bit PCM 单声道 WAV Blob。
 */
function encodeAudioBufferToWav(audioBuffer: AudioBuffer, targetSampleRate = 16000): Blob {
  const numChannels = 1
  const sourceSampleRate = audioBuffer.sampleRate
  const sourceLength = audioBuffer.length

  let monoData: Float32Array

  if (audioBuffer.numberOfChannels === 1) {
    monoData = audioBuffer.getChannelData(0)
  } else {
    const invN = 1 / audioBuffer.numberOfChannels
    monoData = new Float32Array(sourceLength)

    for (let c = 0; c < audioBuffer.numberOfChannels; c++) {
      const channelData = audioBuffer.getChannelData(c)

      for (let i = 0; i < sourceLength; i++) {
        monoData[i] += channelData[i] * invN
      }
    }
  }

  let resampledData: Float32Array

  if (sourceSampleRate === targetSampleRate) {
    resampledData = monoData
  } else {
    const ratio = sourceSampleRate / targetSampleRate
    const targetLength = Math.round(sourceLength / ratio)
    resampledData = new Float32Array(targetLength)

    for (let i = 0; i < targetLength; i++) {
      const srcPos = i * ratio
      const idx0 = Math.floor(srcPos)
      const idx1 = Math.min(idx0 + 1, sourceLength - 1)
      const frac = srcPos - idx0
      resampledData[i] = monoData[idx0] * (1 - frac) + monoData[idx1] * frac
    }
  }

  const sampleCount = resampledData.length
  const bytesPerSample = 2
  const blockAlign = numChannels * bytesPerSample
  const byteRate = targetSampleRate * blockAlign
  const dataSize = sampleCount * bytesPerSample
  const buffer = new ArrayBuffer(44 + dataSize)
  const view = new DataView(buffer)

  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataSize, true)
  writeAscii(view, 8, 'WAVE')

  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, numChannels, true)
  view.setUint32(24, targetSampleRate, true)
  view.setUint32(28, byteRate, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, 16, true)

  writeAscii(view, 36, 'data')
  view.setUint32(40, dataSize, true)

  // 写入 16-bit PCM 采样（限幅至 [-1, 1]）
  let offset = 44

  for (let i = 0; i < sampleCount; i++) {
    const s = clamp(resampledData[i], -1, 1)
    const val = s < 0 ? s * 0x8000 : s * 0x7fff
    view.setInt16(offset, val, true)
    offset += 2
  }

  return new Blob([buffer], { type: 'audio/wav' })
}

function writeAscii(view: DataView, offset: number, str: string): void {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i))
  }
}
