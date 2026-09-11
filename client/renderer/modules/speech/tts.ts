import type { SpeechStyle } from '@ipc/contracts'

import { presentationPorts } from '@/shared/presentation-ports'

import { speechText } from '../../../shared/speech-text'

import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'
import { beginVoicePreparing, endVoicePreparing } from './voice-state'

// 伙伴 TTS 经 `spiritagent:media:tts` REST IPC 调用。

export function stopSpeaking(): void {
  stopAudio()
}

export async function requestSynth(
  text: string,
  voice?: string,
  context?: string,
  persist = false,
  speechStyle?: SpeechStyle
): Promise<string> {
  const spokenText = speechText(text)

  if (!spokenText) {
    return ''
  }

  const res = await window.spiritagent.media.tts({
    text: spokenText,
    speech_style: speechStyle,
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
  persist: boolean,
  onDone?: () => void,
  throwOnError = false,
  speechStyle?: SpeechStyle
): Promise<boolean> {
  if (!speechText(text)) {
    onDone?.()

    return false
  }

  const gen = nextGen()
  beginVoicePreparing()

  try {
    const dataUrl = await requestSynth(text, voice, context, persist, speechStyle)

    if (!isLatestGen(gen)) {
      return false
    }

    // 生命周期回调全权交给 playDataUrl 结算：至多触发一次、只属于当前音频
    // （抢占时由 stopAudio 同步结算旧的）。这里若再比对 synth 自己的 gen，
    // 会被 playDataUrl 内部的两次计数递增甩开，恒为假、回调永远不触发。
    return await playDataUrl(dataUrl, onDone)
  } catch (err) {
    stopAudio()

    if (throwOnError) {
      throw err
    }

    // 环境路径按 DESIGN §7 静默降级为纯文字，但留诊断日志定位供应商故障。
    console.warn('[tts] synthesis failed', err)

    return false
  } finally {
    endVoicePreparing()
  }
}

/** 动态台词（聊天回复 / 主动消息）。只走内存缓存，不落盘。 */
export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, false)
}

/** 需要落盘的合成入口（音色试听、文档化的本地反应池）。按内容寻址落盘，
 *  同一组 (音色, 台词) 只消耗一次云端额度。离线/机械降级边界见 DESIGN §6.3。 */
export async function speakScripted(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, true)
}

/** 聊天窗口里用户主动点击消息气泡下方的「播放」按钮时的入口。语义与
 *  {@link speak} 一致——动态、单次、命中即停——但永远走磁盘缓存，保证同一段
 *  (voice, text) 跨会话只会消耗一次云端额度。失败向上抛：显式操作必须可见。 */
export async function speakChatMessage(
  text: string,
  voice?: string,
  onDone?: () => void,
  speechStyle?: SpeechStyle
): Promise<boolean> {
  return await synth(text, voice, 'chat.replay', true, onDone, true, speechStyle)
}
