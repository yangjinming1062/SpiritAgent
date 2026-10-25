import { useStore } from '@nanostores/react'
import type React from 'react'

import { DirectoryShell } from '@/app/components/directory-shell/directory-shell'
import { ActionsPage } from '@/app/features/actions/actions-page'
import { AppearancePage } from '@/app/features/appearance/appearance-page'
import { ScenePage } from '@/app/features/scene/scene-page'
import { $companionSection, setCompanionSection } from '@/app/navigation/app-navigation'
import { Activity, Brain, Home, Shirt, Users, Video, Volume2 } from '@/shared/lib/icons'
import { SettingsSectionIntro } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { VisualsPage } from '../visuals/visuals-page'

import { MemorySection } from './memory-section'
import { PersonaPage } from './persona-page'
import { VoicePage } from './voice-page'

const ENTRIES = [
  { id: 'profile', icon: Users },
  { id: 'voice', icon: Volume2 },
  { id: 'memory', icon: Brain },
  { id: 'wardrobe', icon: Shirt },
  { id: 'environment', icon: Home },
  { id: 'actions', icon: Activity },
  { id: 'visuals', icon: Video }
] as const

export function CompanionHub(): React.JSX.Element {
  const auth = useStore($auth)
  const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind

  return <CompanionSession key={identity} />
}

function CompanionSession(): React.JSX.Element {
  const section = useStore($companionSection)
  const strings = useStrings()
  const persona = strings.settings.persona

  const boundedFeature = section !== 'profile'

  const page = (): React.ReactNode => {
    switch (section) {
      case 'profile':
        return <PersonaPage />

      case 'visuals':
        return <VisualsPage />

      case 'voice':
        return <VoicePage />

      case 'wardrobe':
        return <AppearancePage />

      case 'environment':
        return <ScenePage />

      case 'actions':
        return <ActionsPage />

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
    <DirectoryShell
      activeId={section}
      ariaLabel={persona.categoriesAria}
      bounded={boundedFeature}
      entries={ENTRIES.map(({ id, icon }) => ({ id, icon, label: persona.sections[id] }))}
      onSelect={setCompanionSection}
    >
      {page()}
    </DirectoryShell>
  )
}
