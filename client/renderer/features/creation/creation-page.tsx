import { useStore } from '@nanostores/react'
import type React from 'react'

import { $creationSection, openCreation } from '@/app/navigation/app-navigation'
import { Activity, Home, Shirt, Video } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'
import type { AppPageProps } from '@/shared/types/app-page'
import { SectionShell } from '@/ui/section-shell/section-shell'
import type { CreationSection } from '@ipc/contracts'

import { ActionsPage } from './actions/actions-page'
import { $creationActivity } from './creation-activity'
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
        {activity.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-accent-line bg-accent-soft/20 px-3 py-2">
            <span className="text-[11px] font-medium text-muted">{studio.activityTitle}</span>
            {activity.slice(0, 4).map(item => (
              <button
                className="rounded-full border border-line-hairline px-2 py-0.5 text-[11px] text-body hover:bg-fill-hover"
                key={item.id}
                onClick={() => void openCreation(item.section)}
                type="button"
              >
                {labels[item.section]} · {item.state}
              </button>
            ))}
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col">{content(section)}</div>
      </div>
    </SectionShell>
  )
}
