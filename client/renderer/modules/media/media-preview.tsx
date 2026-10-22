import type { ReactNode } from 'react'
import { useState } from 'react'

import { useResolvedMediaSrc } from './media-src'

export interface MediaPreviewState {
  /** 解析失败或媒体元素加载失败。 */
  failed: boolean
  /** 媒体元素加载失败时调用。 */
  onError: () => void
  /** 重试：重建预览，未成功的解析因此重新发起。 */
  onRetry: () => void
  /** 已解析的媒体地址；未就绪时为 undefined。 */
  src: string | undefined
  /** 解析状态。 */
  status: 'failed' | 'loading' | 'ready'
}

export interface MediaPreviewProps {
  children: (state: MediaPreviewState) => ReactNode
  type: 'image' | 'video'
  url: string
}

/**
 * 媒体预览壳：解析媒体地址并跟踪元素加载失败，呈现由调用方按 state 决定。
 * 重试会重建预览，失败过的解析因此重新发起；地址换新后失败态不延续。
 */
export function MediaPreview({ children, type, url }: MediaPreviewProps): React.JSX.Element {
  const [attempt, setAttempt] = useState(0)

  return (
    <MediaPreviewBody key={attempt} onRetry={() => setAttempt(value => value + 1)} type={type} url={url}>
      {children}
    </MediaPreviewBody>
  )
}

function MediaPreviewBody({
  children,
  onRetry,
  type,
  url
}: {
  children: (state: MediaPreviewState) => ReactNode
  onRetry: () => void
  type: 'image' | 'video'
  url: string
}): React.JSX.Element {
  const media = useResolvedMediaSrc({ type, url })
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const failed = media.status === 'failed' || failedUrl === url

  return (
    <>
      {children({
        failed,
        onError: () => setFailedUrl(url),
        onRetry,
        src: media.status === 'ready' ? media.src : undefined,
        status: media.status
      })}
    </>
  )
}
