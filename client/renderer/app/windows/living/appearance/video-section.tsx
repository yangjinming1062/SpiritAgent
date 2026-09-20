import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

import { $videoPack, $videoPackStatus, hydrateVideoPack } from '@/modules/character'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { OutfitSection } from './outfit-section'

// 外观页视频分区：承载外观参考（着装）管理；顶部显示视频形象的就绪状态——
// 已激活包显示版本信息，未就绪时明确提示占位，不显示不可用的生成入口，不误报支持。
export function VideoSection(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const pack = useStore($videoPack)
  const status = useStore($videoPackStatus)
  const t = useStrings().living.appearance

  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateVideoPack()
    }
  }, [authKind])

  const statusLine =
    status === 'ready' && pack ? t.videoReady(pack.manifest.pack_version, pack.manifest.clips.length) : t.videoNotReady

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mx-4 mt-3 rounded-xl border border-line-hairline bg-surface-card px-3.5 py-2.5">
        <span className="text-xs text-body">{statusLine}</span>
      </div>
      <OutfitSection />
    </div>
  )
}
