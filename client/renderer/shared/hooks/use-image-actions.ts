import { useCallback, useRef, useState } from 'react'

import { imageUrlForNativeClipboard } from '@/shared/lib/image-clipboard'
import { log } from '@/shared/lib/log'
import { fileApi } from '@/shared/platform/desktop'

import { useAsyncGuard } from './use-async-guard'

type ImageActionName = 'copy' | 'save'

// 图片复制与另存为的共用状态：每次新操作先清除上一次的“已复制”与错误；失败记日志，并在 error 中标明是哪个操作。
export function useImageActions(): {
  copied: boolean
  copy: (url: string) => Promise<void>
  error: ImageActionName | null
  /** 清除“已复制”与错误提示（宿主面板重新打开时用）。 */
  reset: () => void
  save: (url: string, defaultName?: string) => Promise<void>
} {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<ImageActionName | null>(null)
  const revision = useRef(0)
  const copyRevision = useRef(0)
  const begin = useAsyncGuard()

  const reset = useCallback((): void => {
    revision.current += 1
    setError(null)
    setCopied(false)
  }, [])

  const run = async (name: ImageActionName, task: (isLive: () => boolean) => Promise<void>): Promise<void> => {
    reset()
    const version = revision.current
    const isLive = begin()
    const isCurrent = (): boolean => isLive() && version === revision.current

    try {
      await task(isLive)

      if (isCurrent()) {
        setCopied(name === 'copy')
      }
    } catch (cause) {
      log.warn('image-actions', `${name} image failed`, cause)

      if (isCurrent()) {
        setError(name)
      }
    }
  }

  return {
    copied,
    copy: url => {
      const version = ++copyRevision.current

      return run('copy', async isLive => {
        const clipboardUrl = await imageUrlForNativeClipboard(url)

        if (isLive() && version === copyRevision.current) {
          await fileApi().copyImage({ url: clipboardUrl })
        }
      })
    },
    error,
    reset,
    save: (url, defaultName) =>
      run('save', async () => {
        await fileApi().saveImage({ defaultName: defaultName || undefined, url })
      })
  }
}
