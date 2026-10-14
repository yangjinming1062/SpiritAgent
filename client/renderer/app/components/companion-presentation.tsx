import { useStore } from '@nanostores/react'
import type React from 'react'

import {
  $actionCatalogStatus,
  $videoGenStages,
  $videoGenState,
  hydrateActionCatalog,
  hydrateVideoPack,
  resolveCompanionPresentation
} from '@/modules/character'
import { EggStage } from '@/modules/character/rendering'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

type CompanionPresentation = ReturnType<typeof resolveCompanionPresentation>

// 视频就绪挂视频层，否则落蛋形并给出真实状态（DESIGN「呈现与降级」）。精灵窗与完整入口的侧边伙伴共用。
export function useCompanionPresentation(): CompanionPresentation {
  const catalogStatus = useStore($actionCatalogStatus)
  const generationState = useStore($videoGenState)
  const generationStages = useStore($videoGenStages)
  // 兜底文案由 resolveCompanionPresentation 按当前语言读取，订阅语言使其随切换更新。
  useStrings()

  // 蛋形兜底无着装语境：取任一在途包的阶段（引导期只有一个包，正常情况即当前包）。
  const stage = Object.values(generationStages)[0] ?? null

  return resolveCompanionPresentation({ catalogStatus, generationStage: stage, generationState })
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
          void requestOpenSurface({ page: 'appearance' })

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
