import { useStore } from '@nanostores/react'
import type React from 'react'

import { $creationSection, openCreation } from '@/app/navigation/app-navigation'
import { Activity, Home, Shirt, Video } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import type { AppPageProps } from '@/shared/types/app-page'
import { SectionShell } from '@/ui/section-shell/section-shell'
import type { CreationSection } from '@ipc/contracts'

import { ActionsPage } from './actions/actions-page'
import { $creationActivity, type CreationActivityItem } from './creation-activity'
import { ScenePage } from './environment/scene-page'
import { VisualsPage } from './visuals/visuals-page'
import { AppearancePage } from './wardrobe/appearance-page'

const ENTRIES = [
  { id: 'visuals', icon: Video },
  { id: 'wardrobe', icon: Shirt },
  { id: 'environment', icon: Home },
  { id: 'actions', icon: Activity }
] as const

/** 创作页：影像、造型、环境与动作按"一起做什么"组织，素材归属不再是独立入口。 */
export function CreationPage(_props: AppPageProps): React.JSX.Element {
  const section = useStore($creationSection)
  const activity = useStore($creationActivity)
  const strings = useStrings()
  const labels = strings.settings.persona.sections
  const studio = strings.studio
  const attention = activity.filter(item => item.group === 'attention')
  const working = activity.filter(item => item.group === 'working')

  const openTarget = (item: CreationActivityItem): void => {
    void openCreation(item.section, {
      ...(item.actionId ? { actionId: item.actionId } : {}),
      ...(item.sceneId ? { sceneId: item.sceneId } : {})
    })
  }

  const chip = (item: CreationActivityItem): React.JSX.Element => (
    <button
      className={cn(
        'rounded-full border px-2 py-0.5 text-[11px] hover:bg-fill-hover',
        item.group === 'attention' ? 'border-warning/60 text-warning' : 'border-line-hairline text-body'
      )}
      key={item.id}
      onClick={() => openTarget(item)}
      type="button"
    >
      {labels[item.section]} · {activityStateText(item, strings)}
    </button>
  )

  const content = (value: CreationSection): React.ReactNode => {
    switch (value) {
      case 'visuals':
        return <VisualsPage />

      case 'wardrobe':
        return <AppearancePage />

      case 'environment':
        return <ScenePage />

      case 'actions':
        return <ActionsPage />
    }
  }

  return (
    <SectionShell
      activeId={section}
      ariaLabel={strings.settings.persona.categoriesAria}
      bounded
      entries={ENTRIES.map(({ id, icon }) => ({ id, icon, label: labels[id] }))}
      onSelect={next => void openCreation(next)}
    >
      <div className="flex min-h-0 flex-1 flex-col">
        {attention.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-warning/60 bg-warning/10 px-3 py-2">
            <span className="text-[11px] font-medium text-warning">{studio.attentionTitle}</span>
            {attention.slice(0, 4).map(chip)}
            {attention.length > 4 && <span className="text-[11px] text-muted">+{attention.length - 4}</span>}
          </div>
        )}
        {working.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-line-hairline bg-fill-faint/40 px-3 py-2">
            <span className="text-[11px] font-medium text-muted">{studio.activityTitle}</span>
            {working.slice(0, 4).map(chip)}
            {working.length > 4 && <span className="text-[11px] text-muted">+{working.length - 4}</span>}
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col">{content(section)}</div>
      </div>
    </SectionShell>
  )
}

/** 活动条状态文案：内部状态码按所属分区映射为用户可读词，未识别码原样显示。 */
function activityStateText(item: CreationActivityItem, strings: ReturnType<typeof useStrings>): string {
  if (item.section === 'actions') {
    return strings.actions.statusLabels[item.state as keyof typeof strings.actions.statusLabels] ?? item.state
  }

  if (item.section === 'environment') {
    if (item.state === 'creation_unknown') {
      return strings.creation.unknown
    }

    return item.state === 'waiting_upload'
      ? strings.living.scene.waitingUploadOverlay
      : strings.living.scene.pendingOverlay
  }

  if (item.state === 'review_pending') {
    return strings.living.posts.mediaRecoveryStatuses.review_pending
  }

  return strings.visuals.jobState[item.state] ?? item.state
}
