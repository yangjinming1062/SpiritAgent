import { useCallback, useState } from 'react'

import { imageUrlForNativeClipboard } from '@/shared/lib/image-clipboard'
import { log } from '@/shared/lib/log'

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

  const reset = useCallback((): void => {
    setError(null)
    setCopied(false)
  }, [])

  const run = async (name: ImageActionName, task: () => Promise<void>): Promise<void> => {
    reset()

    try {
      await task()
    } catch (cause) {
      log.warn('image-actions', `${name} image failed`, cause)
      setError(name)
    }
  }

  return {
    copied,
    copy: url =>
      run('copy', async () => {
        await window.spiritagent.copyImage({ url: await imageUrlForNativeClipboard(url) })
        setCopied(true)
      }),
    error,
    reset,
    save: (url, defaultName) =>
      run('save', async () => {
        await window.spiritagent.saveImage({ defaultName: defaultName || undefined, url })
      })
  }
}
