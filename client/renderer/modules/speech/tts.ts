import { speechText } from '@client-shared/speech-text'

import { log } from '@/shared/lib/log'
import { presentationPorts } from '@/shared/presentation-ports'

import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'
import { beginVoicePreparing, endVoicePreparing } from './voice-state'

// 伙伴 TTS 经 `spiritagent:media:tts` REST IPC 调用。

export function stopSpeaking(): void {
  stopAudio()
}

/** 取音频并播放，返回是否完整播完。全程持有语音准备态；取回时已被更新的播放接管则放弃。取音频或播放抛错交给 onError，latest 表示此时仍是最新一次播放。 */
export async function playPrepared(
  fetchClip: () => Promise<{ dataUrl: string }>,
  onError: (error: unknown, latest: boolean) => void
): Promise<boolean> {
  const gen = nextGen()
  beginVoicePreparing()

  try {
    const { dataUrl } = await fetchClip()

    if (!isLatestGen(gen)) {
      return false
    }

    return (await playDataUrl(dataUrl)) === 'completed'
  } catch (error) {
    onError(error, isLatestGen(gen))

    return false
  } finally {
    endVoicePreparing()
  }
}

async function synth(
  text: string,
  voice: string | undefined,
  context: string | undefined,
  persist: boolean
): Promise<boolean> {
  const spokenText = speechText(text)

  if (!spokenText) {
    return false
  }

  return await playPrepared(
    () =>
      window.spiritagent.media.tts({
        text: spokenText,
        voice: voice ?? presentationPorts().$companionVoiceId.get(),
        context: context ?? null,
        persist
      }),
    (error, latest) => {
      // 已被更新的播放接管时不能停掉它。
      if (latest) {
        stopAudio()
      }

      // 环境路径按 DESIGN「语音保存与恢复」静默降级为纯文字，但留诊断日志定位供应商故障。
      log.warn('tts', 'synthesis failed', error)
    }
  )
}

/** 直接互动与角色行为台词。只走内存缓存，不落盘。 */
export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, false)
}

/** 需要落盘的合成入口（音色试听、文档化的本地反应池）。按内容寻址落盘，同一组 (音色, 台词) 只消耗一次云端额度。离线/机械降级边界见 DESIGN「拖拽与直接交互」。 */
export async function speakScripted(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, true)
}
