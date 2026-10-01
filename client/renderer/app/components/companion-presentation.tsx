import { useStore } from '@nanostores/react'
import type React from 'react'

import {
  $actionCatalogStatus,
  $videoGenStage,
  $videoGenState,
  EggStage,
  hydrateActionCatalog,
  hydrateVideoPack,
  resolveCompanionPresentation
} from '@/modules/character'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

type CompanionPresentation = ReturnType<typeof resolveCompanionPresentation>

// 视频就绪挂视频层，否则落蛋形并给出真实状态（DESIGN「呈现与降级」）。桌面精灵与完整入口的侧边伙伴共用。
export function useCompanionPresentation(): CompanionPresentation {
  const catalogStatus = useStore($actionCatalogStatus)
  const generationState = useStore($videoGenState)
  const generationStage = useStore($videoGenStage)
  // 兜底文案由 resolveCompanionPresentation 按当前语言读取，订阅语言使其随切换更新。
  useStrings()

  return resolveCompanionPresentation({ catalogStatus, generationStage, generationState })
}

// 蛋形兜底：失败时进入外观页重试，其余状态重新拉取动作目录与视频包。
export function CompanionEgg({
  presentation,
  size,
  windowId
}: {
  presentation: Extract<CompanionPresentation, { renderer: 'fallback' }>
  size?: number | string
  windowId?: number
}): React.JSX.Element {
  return (
    <EggStage
      hasRecoveryAction={presentation.fallbackActionAvailable}
      message={presentation.fallbackMessage}
      onStatusAction={() => {
        if (presentation.fallbackStatus === 'failed') {
          void requestOpenSurface('living', { view: 'appearance' })

          return
        }

        void hydrateActionCatalog(true)
        void hydrateVideoPack(true)
      }}
      size={size}
      status={presentation.fallbackStatus}
      windowId={windowId}
    />
  )
}
