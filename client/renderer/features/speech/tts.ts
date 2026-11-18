import { log } from '@/shared/lib/log'
import { mediaApi } from '@/shared/platform/desktop'
import { presentationPorts } from '@/shared/presentation-ports'
import { speechText } from '@client-shared/speech-text'

import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'

// 伙伴 TTS 经 `spiritagent:media:tts` REST IPC 调用。

export function stopSpeaking(): void {
  stopAudio()
}

/** 取音频并播放，返回是否完整播完；取回时已被更新的播放接管则放弃。取音频或播放抛错交给 onError，latest 表示此时仍是最新一次播放。 */
async function playPrepared(
  fetchClip: () => Promise<{ dataUrl: string }>,
  onError: (error: unknown, latest: boolean) => void,
  signal?: AbortSignal
): Promise<boolean> {
  if (signal?.aborted) {
    return false
  }

  const gen = nextGen()
  let onAbort: (() => void) | undefined

  const aborted = signal
    ? new Promise<null>(resolve => {
        onAbort = () => resolve(null)
        signal.addEventListener('abort', onAbort, { once: true })
      })
    : null

  try {
    const clip = await (aborted ? Promise.race([fetchClip(), aborted]) : fetchClip())

    if (!clip || signal?.aborted || !isLatestGen(gen)) {
      return false
    }

    return (await playDataUrl(clip.dataUrl, { signal })) === 'completed'
  } catch (error) {
    if (!signal?.aborted) {
      onError(error, isLatestGen(gen))
    }

    return false
  } finally {
    if (onAbort) {
      signal?.removeEventListener('abort', onAbort)
    }
  }
}

async function synth(
  text: string,
  voice: string | undefined,
  context: string | undefined,
  persist: boolean,
  signal?: AbortSignal
): Promise<boolean> {
  const spokenText = speechText(text)

  if (!spokenText) {
    return false
  }

  return await playPrepared(
    () =>
      mediaApi().media.tts({
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
    },
    signal
  )
}

/** 直接互动与角色行为台词。只走内存缓存，不落盘。 */
export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, false)
}

/** 固定音色试听按音色和样本内容落盘缓存。 */
export async function playVoiceSample(
  text: string,
  voice?: string,
  context?: string,
  signal?: AbortSignal
): Promise<boolean> {
  return await synth(text, voice, context, true, signal)
}
