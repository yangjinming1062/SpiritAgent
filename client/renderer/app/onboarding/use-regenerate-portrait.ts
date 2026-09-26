import { useCallback, useEffect, useRef, useState } from 'react'

import {
  $regenFeedback,
  applyPortrait,
  awaitAvatarRegeneration,
  type PickedImage,
  pushPortraitEntry
} from '@/modules/character'
import { useGatewayRequest } from '@/shared'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { currentClearEpoch } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { playOnboardingAudio } from './onboarding-audio'

interface UseRegeneratePortraitOptions {
  refImage?: PickedImage | null
  /** 有身份参考时仅提供光线与构图；否则作为唯一参考图。 */
  presentationRef?: PickedImage | null
  playAudioOnSuccess?: boolean
  feedback?: string
  onRegenerated?: (urls: { avatar: string; id: number | null }) => void
  onError?: (message: string) => void
}

interface PortraitResponse {
  asset_url?: string | null
  id?: number
  job_id?: string
  queued?: boolean
  error?: string
}

interface UseRegeneratePortraitResult {
  /** null 表示请求已失效或已有请求在途；false 表示失败且保留输入。 */
  generate: (feedback?: string, overrideRef?: PickedImage | null) => Promise<boolean | null>
  regenerate: (feedback?: string, overrideRef?: PickedImage | null) => Promise<void>
  edit: (feedback?: string) => Promise<void>
  reload: () => Promise<void>
  busy: boolean
}

// 首次生成、重生与微调共用单次提交、预览落地和描述清理；不重发结果未知的付费请求。
export function useRegeneratePortrait(options: UseRegeneratePortraitOptions = {}): UseRegeneratePortraitResult {
  const { requestGateway } = useGatewayRequest()
  const [busy, setBusy] = useState(false)
  const mountedRef = useRef(false)
  const runningRef = useRef(false)
  const operationRef = useRef(0)

  const {
    refImage,
    presentationRef,
    playAudioOnSuccess = false,
    feedback: optionFeedback,
    onRegenerated,
    onError
  } = options

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      operationRef.current += 1
      runningRef.current = false
    }
  }, [])

  const run = useCallback(
    async (
      mode: 'generate' | 'regenerate' | 'edit' | 'reload',
      callFeedback?: string,
      overrideRef?: PickedImage | null
    ): Promise<boolean | null> => {
      const auth = $auth.get()

      if (!mountedRef.current || runningRef.current || auth.kind !== 'authenticated') {
        return null
      }

      const draft = $regenFeedback.get()
      // 显式空串表示本次不附描述，不回退到其他来源的旧输入。
      const feedback = (callFeedback ?? optionFeedback ?? draft).trim() || undefined

      if (mode === 'edit' && !feedback) {
        return null
      }

      const epoch = currentClearEpoch()
      const sessionId = auth.snapshot.sessionId
      const operation = ++operationRef.current

      const isCurrent = (): boolean => {
        const current = $auth.get()

        return (
          mountedRef.current &&
          operationRef.current === operation &&
          currentClearEpoch() === epoch &&
          current.kind === 'authenticated' &&
          current.snapshot.sessionId === sessionId
        )
      }

      runningRef.current = true
      setBusy(true)

      try {
        const identityRef = overrideRef !== undefined ? overrideRef : refImage
        const presentation = mode === 'generate' ? null : presentationRef
        const primaryRef = identityRef ?? presentation
        const secondaryRef = identityRef ? presentation : null
        let result: PortraitResponse | null

        if (mode === 'reload') {
          result = await window.spiritagent.api<PortraitResponse>({ path: '/api/companion/avatar', method: 'GET' })
        } else if (mode !== 'edit' && (primaryRef || mode === 'generate')) {
          result = await window.spiritagent.api<PortraitResponse>({
            method: 'POST',
            path: primaryRef ? '/api/companion/avatar/from-image' : '/api/companion/avatar',
            body: primaryRef
              ? {
                  content_type: primaryRef.contentType,
                  image: primaryRef.base64,
                  description: feedback,
                  ...(secondaryRef && {
                    presentation_content_type: secondaryRef.contentType,
                    presentation_image: secondaryRef.base64
                  })
                }
              : { feedback }
          })
        } else {
          const queued = await requestGateway<PortraitResponse>(
            'avatar.regenerate',
            { feedback, mode },
            { retryOnReconnect: false }
          )

          if (!isCurrent()) {
            return null
          }

          result = queued?.queued && queued.job_id ? await awaitAvatarRegeneration(queued.job_id) : queued
        }

        if (!isCurrent()) {
          return null
        }

        if (result?.error) {
          onError?.(result.error)

          return false
        }

        if (!result?.asset_url) {
          onError?.('未收到生成结果，已保留描述。请重新加载查看头像后再决定是否重试。')

          return false
        }

        const applied = await applyPortrait({ assetUrl: result.asset_url, id: result.id }, isCurrent)

        if (!isCurrent()) {
          return null
        }

        if (!applied.avatar) {
          onError?.('头像已保存，但预览加载失败，已保留描述。请重新加载查看。')

          return false
        }

        pushPortraitEntry({ assetUrl: result.asset_url, avatarId: result.id ?? null, portraitUrl: applied.avatar })

        if (mode !== 'reload' && $regenFeedback.get() === draft) {
          $regenFeedback.set('')
        }

        onRegenerated?.({ avatar: applied.avatar, id: result.id ?? null })

        if ((mode === 'regenerate' || mode === 'edit') && playAudioOnSuccess) {
          void playOnboardingAudio('onboarding.portrait.regenerate')
        }

        return true
      } catch (error) {
        if (!isCurrent()) {
          return null
        }

        onError?.(backendDetailMessage(error, '生成未完成，已保留描述。请重新加载查看头像后再决定是否重试。'))

        return false
      } finally {
        if (operationRef.current === operation) {
          runningRef.current = false

          if (mountedRef.current) {
            setBusy(false)
          }
        }
      }
    },
    [refImage, presentationRef, optionFeedback, onRegenerated, onError, playAudioOnSuccess, requestGateway]
  )

  const generate = useCallback(
    (feedback?: string, overrideRef?: PickedImage | null) => run('generate', feedback, overrideRef),
    [run]
  )

  const regenerate = useCallback(
    async (feedback?: string, overrideRef?: PickedImage | null): Promise<void> => {
      await run('regenerate', feedback, overrideRef)
    },
    [run]
  )

  const edit = useCallback(
    async (feedback?: string): Promise<void> => {
      await run('edit', feedback)
    },
    [run]
  )

  const reload = useCallback(async (): Promise<void> => {
    await run('reload')
  }, [run])

  return { busy, generate, regenerate, edit, reload }
}
