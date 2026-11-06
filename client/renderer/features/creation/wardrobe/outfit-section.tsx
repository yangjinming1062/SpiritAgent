import type React from 'react'

import { SelfSourceImageFlow } from '@/features/character'
import { type AssetLibrarySearch, PortraitLightbox } from '@/ui'

import { OutfitEditor } from './outfit-editor'
import { OutfitList } from './outfit-list'
import { OutfitReviewTasks } from './outfit-review-tasks'
import { useOutfitSection } from './use-outfit-section'

interface OutfitSectionProps {
  onSelectOutfit: (id: string) => void
  search: AssetLibrarySearch
}

/** 造型页：列表与制作区共用同一模型，页面只负责装配与自备图流程。 */
export function OutfitSection({ onSelectOutfit, search }: OutfitSectionProps): React.JSX.Element {
  const model = useOutfitSection({ onSelectOutfit, search })

  const {
    designing,
    session,
    zoomUrl,
    setZoomUrl,
    t,
    dict,
    outfitSelfSourceOpen,
    setOutfitSelfSourceOpen,
    adoptOutfitSelfSource,
    fetchOutfitSelfSourcePrompt,
    outfitSelfSourceReferences,
    hasInput,
    inputRef,
    sendCreation,
    sendRevise
  } = model

  return (
    <div className="flex min-h-0 flex-1 flex-col border-b border-line-hairline bg-surface-panel">
      <OutfitReviewTasks refreshKey={session.busy} />
      {designing ? <OutfitEditor model={model} /> : <OutfitList model={model} />}

      {zoomUrl && <PortraitLightbox name={t.imageAlt} onClose={() => setZoomUrl(null)} url={zoomUrl} />}

      <SelfSourceImageFlow
        adopt={adoptOutfitSelfSource}
        fetchPrompt={fetchOutfitSelfSourcePrompt}
        onClose={() => setOutfitSelfSourceOpen(false)}
        onUseAi={() => {
          setOutfitSelfSourceOpen(false)

          if (session.draft) {
            sendRevise('regenerate')

            return
          }

          // 无可发送内容时会静默拒绝——聚焦输入框把用户带回设计流程，避免点按钮毫无反馈。
          if (!hasInput) {
            inputRef.current?.focus()

            return
          }

          sendCreation()
        }}
        open={outfitSelfSourceOpen}
        promptBlockedReason={!session.draft && !hasInput ? t.design.promptRequiresInput : undefined}
        referenceImages={outfitSelfSourceReferences}
        title={session.draft ? dict.generationActions.regenerate : t.startAction}
      />
    </div>
  )
}
