import { useCallback, useEffect, useRef, useState } from 'react'

import { $regenFeedback, applyPortrait, type PickedImage, pushPortraitEntry } from '@/modules/character'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, ipcErrorStatus, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../shared/api.generated'

interface UseRegeneratePortraitOptions {
  refImage?: PickedImage | null
  /** 只在已有身份参考时随重绘提交，仅调整光线、色调与构图；没有身份参考时不使用。 */
  presentationRef?: PickedImage | null
  onRegenerated?: (urls: { avatar: string; id: number | null }) => void
  onError?: (message: string) => void
}

type PortraitResponse = ApiSchemas['AvatarAssetResponse']

interface UseRegeneratePortraitResult {
  /** null 表示请求已失效或已有请求在途；false 表示失败且保留输入。 */
  generate: () => Promise<boolean | null>
  regenerate: () => Promise<void>
  edit: () => Promise<void>
  reload: () => Promise<void>
  busy: boolean
  resultUnknown: boolean
  previewUnavailable: boolean
}

// 首次生成、重生与微调共用单次提交、预览落地和描述清理；不重发结果未知的付费请求。
export function useRegeneratePortrait(options: UseRegeneratePortraitOptions = {}): UseRegeneratePortraitResult {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<'unknown' | 'preview' | null>(null)
  const mountedRef = useRef(false)
  const runningRef = useRef(false)
  const operationRef = useRef(0)

  const { refImage, presentationRef, onRegenerated, onError } = options

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      operationRef.current += 1
      runningRef.current = false
    }
  }, [])

  const run = useCallback(
    async (mode: 'generate' | 'regenerate' | 'edit' | 'reload'): Promise<boolean | null> => {
      const scope = captureAuthScope()

      if (!mountedRef.current || runningRef.current || !scope) {
        return null
      }

      const draft = $regenFeedback.get()
      const feedback = draft.trim() || undefined

      if (mode === 'edit' && !feedback) {
        return null
      }

      const operation = ++operationRef.current
      // 卸载会递增 operationRef，isCurrent 因此同时覆盖卸载。
      const isCurrent = (): boolean => operationRef.current === operation && scope()

      runningRef.current = true
      setBusy(true)

      try {
        const primaryRef = refImage
        const secondaryRef = mode === 'generate' ? null : presentationRef
        let result: PortraitResponse | null

        if (mode === 'reload') {
          result = await requestResource('companion.get_avatar')
        } else if (mode !== 'edit' && (primaryRef || mode === 'generate')) {
          result = await requestResource('companion.post_avatar', {
            body: primaryRef
              ? {
                  source: 'reference',
                  content_type: primaryRef.contentType,
                  image: primaryRef.base64,
                  description: feedback,
                  ...(secondaryRef && {
                    presentation_content_type: secondaryRef.contentType,
                    presentation_image: secondaryRef.base64
                  })
                }
              : { source: 'generate', feedback }
          })
        } else {
          result = await requestResource('companion.post_avatar', { body: { source: 'generate', feedback, mode } })
        }

        if (!isCurrent()) {
          return null
        }

        if (!result?.asset_url) {
          setFailure(mode === 'reload' ? null : 'unknown')
          onError?.('未收到生成结果，已保留描述。请重新加载查看头像后再决定是否重试。')

          return false
        }

        const applied = await applyPortrait({ assetUrl: result.asset_url, id: result.id }, isCurrent)

        if (!isCurrent()) {
          return null
        }

        if (!applied.avatar) {
          setFailure('preview')
          onError?.('头像已保存，但预览加载失败，已保留描述。请重新加载查看。')

          return false
        }

        pushPortraitEntry({ assetUrl: result.asset_url, avatarId: result.id ?? null, portraitUrl: applied.avatar })

        if (mode !== 'reload' && $regenFeedback.get() === draft) {
          $regenFeedback.set('')
        }

        setFailure(null)
        onRegenerated?.({ avatar: applied.avatar, id: result.id ?? null })

        return true
      } catch (error) {
        if (!isCurrent()) {
          return null
        }

        if (mode === 'reload' && ipcErrorStatus(error) === 404) {
          setFailure(null)
          onError?.('尚未找到已生成的头像，可以重新生成或使用自己的图片。')
        } else {
          if (mode !== 'reload') {
            setFailure(isClientErrorIpc(error) ? null : 'unknown')
          }

          onError?.(backendDetailMessage(error, '生成未完成，已保留描述。请重新加载查看头像后再决定是否重试。'))
        }

        return false
      } finally {
        if (operationRef.current === operation) {
          runningRef.current = false
          setBusy(false)
        }
      }
    },
    [refImage, presentationRef, onRegenerated, onError]
  )

  const generate = useCallback(() => run('generate'), [run])

  const regenerate = useCallback(async (): Promise<void> => {
    await run('regenerate')
  }, [run])

  const edit = useCallback(async (): Promise<void> => {
    await run('edit')
  }, [run])

  const reload = useCallback(async (): Promise<void> => {
    await run('reload')
  }, [run])

  return {
    busy,
    resultUnknown: failure === 'unknown',
    previewUnavailable: failure === 'preview',
    generate,
    regenerate,
    edit,
    reload
  }
}
