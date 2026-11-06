import { useCallback, useEffect, useRef, useState } from 'react'

import { playDataUrl, sampleLine, speakScripted, type VoiceDesignPreview } from '@/features/speech'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { failureText } from '@/shared/lib/ipc-error'
import { backendApi } from '@/shared/platform/desktop'
import { useStrings } from '@/shared/strings'

/** 试听播放：持唯一播放身份与中断控制器，失去活动资格即释放，迟到结果不再出声。 */
export function useVoicePreview({
  active,
  name,
  onBeforePlay,
  viewEligible
}: {
  active: boolean
  name: string
  onBeforePlay: () => void
  viewEligible: () => boolean
}) {
  const t = useStrings().voice
  const begin = useAsyncGuard()
  const [playing, setPlaying] = useState<string | null>(null)
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const previewAbort = useRef<AbortController | null>(null)
  const playbackGeneration = useRef(0)

  const stop = useCallback(() => {
    playbackGeneration.current += 1
    previewAbort.current?.abort()
    previewAbort.current = null
    setPlaying(null)
  }, [])

  useEffect(() => {
    if (!active) {
      stop()
    }

    return stop
  }, [active, stop])

  const beginPlayback = (): AbortSignal | null => {
    if (!viewEligible()) {
      return null
    }

    stop()
    const controller = new AbortController()
    previewAbort.current = controller

    return controller.signal
  }

  const play = async (
    key: string,
    playAudio: (signal: AbortSignal, live: () => boolean) => Promise<void>
  ): Promise<void> => {
    if (playing === key) {
      stop()

      return
    }

    const signal = beginPlayback()

    if (!signal) {
      return
    }

    const mounted = begin()
    const live = (): boolean => !signal.aborted && mounted() && viewEligible()
    setPlaybackError(null)
    onBeforePlay()
    setPlaying(key)

    try {
      await playAudio(signal, live)
    } catch (failure) {
      if (live()) {
        setPlaybackError(failureText(failure, t.previewFailed))
      }
    } finally {
      if (live()) {
        setPlaying(null)
      }
    }
  }

  const previewAudio = (url: string): Promise<void> =>
    play(url, async (signal, live) => {
      const dataUrl = url.startsWith('data:') ? url : await backendApi().apiAsset({ url, preferCache: true })

      if (!dataUrl) {
        throw new Error(t.previewFailed)
      }

      if (live() && (await playDataUrl(dataUrl, { signal })) === 'failed') {
        throw new Error(t.previewFailed)
      }
    })

  const previewFixed = (id: string): Promise<void> =>
    play(id, async signal => {
      await speakScripted(sampleLine(name), id, 'voice.preview', signal)
    })

  // 先制作再试听：生成期间用户切页或重新播放都不能让旧预览出声。
  const previewCreation = (blocked: boolean, create: () => Promise<VoiceDesignPreview | null>): void => {
    if (blocked) {
      return
    }

    stop()
    setPlaybackError(null)
    onBeforePlay()

    const mounted = begin()
    const version = playbackGeneration.current

    void create().then(preview => {
      if (preview && mounted() && viewEligible() && version === playbackGeneration.current) {
        void previewAudio(preview.trialAudioDataUrl)
      }
    })
  }

  return { playbackError, playing, previewAudio, previewCreation, previewFixed, setPlaybackError, stop }
}
