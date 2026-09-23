import { speechText } from '@client-shared/speech-text'

import { presentationPorts } from '@/shared/presentation-ports'

import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'
import { beginVoicePreparing, endVoicePreparing } from './voice-state'

// 伙伴 TTS 经 `spiritagent:media:tts` REST IPC 调用。

export function stopSpeaking(): void {
  stopAudio()
}

export async function requestSynth(text: string, voice?: string, context?: string, persist = false): Promise<string> {
  const spokenText = speechText(text)

  if (!spokenText) {
    return ''
  }

  const res = await window.spiritagent.media.tts({
    text: spokenText,
    voice: voice ?? presentationPorts().$companionVoiceId.get(),
    context: context ?? null,
    persist
  })

  return res.dataUrl
}

async function synth(
  text: string,
  voice: string | undefined,
  context: string | undefined,
  persist: boolean
): Promise<boolean> {
  if (!speechText(text)) {
    return false
  }

  const gen = nextGen()
  beginVoicePreparing()

  try {
    const dataUrl = await requestSynth(text, voice, context, persist)

    if (!isLatestGen(gen)) {
      return false
    }

    return (await playDataUrl(dataUrl)) === 'completed'
  } catch (err) {
    stopAudio()

    // 环境路径按 DESIGN §7 静默降级为纯文字，但留诊断日志定位供应商故障。
    console.warn('[tts] synthesis failed', err)

    return false
  } finally {
    endVoicePreparing()
  }
}

/** 直接互动与角色行为台词。只走内存缓存，不落盘。 */
export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, false)
}

/** 需要落盘的合成入口（音色试听、文档化的本地反应池）。按内容寻址落盘，
 *  同一组 (音色, 台词) 只消耗一次云端额度。离线/机械降级边界见 DESIGN §6.3。 */
export async function speakScripted(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, true)
}
