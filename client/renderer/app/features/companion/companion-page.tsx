import { useStore } from '@nanostores/react'
import type React from 'react'

import type { AppPageProps } from '@/app/components/app-content-page'
import { SectionShell } from '@/app/components/section-shell/section-shell'
import { $companionSection, openCompanion } from '@/app/navigation/app-navigation'
import { Brain, Users, Volume2 } from '@/shared/lib/icons'
import { SettingsSectionIntro } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import type { CompanionSection } from '@ipc/contracts'

import { MemorySection } from './memory-section'
import { PersonaPage } from './persona-page'
import { VoicePage } from './voice-page'

const ENTRIES = [
  { id: 'profile', icon: Users },
  { id: 'voice', icon: Volume2 },
  { id: 'memory', icon: Brain }
] as const

/** 伙伴页只保留"它是谁"：资料、声音与记忆；创作相关分区已在创作页。 */
export function CompanionHub(_props: AppPageProps): React.JSX.Element {
  const section = useStore($companionSection)
  const strings = useStrings()
  const persona = strings.settings.persona

  const content = (value: CompanionSection): React.ReactNode => {
    switch (value) {
      case 'profile':
        return <PersonaPage />

      case 'voice':
        return <VoicePage />

      case 'memory':
        return (
          <div className="flex min-h-0 flex-1 flex-col pb-4">
            <SettingsSectionIntro hint={persona.memoryIntro} title={persona.memoriesTitle} />
            <MemorySection />
          </div>
        )
    }
  }

  return (
    <SectionShell
      activeId={section}
      ariaLabel={persona.categoriesAria}
      bounded={section !== 'profile'}
      entries={ENTRIES.map(({ id, icon }) => ({ id, icon, label: persona.sections[id] }))}
      onSelect={next => void openCompanion(next)}
    >
      {content(section)}
    </SectionShell>
  )
}
