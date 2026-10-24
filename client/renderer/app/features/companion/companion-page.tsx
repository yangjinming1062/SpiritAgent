import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { DirectoryShell } from '@/app/components/directory-shell/directory-shell'
import { ActionsPage } from '@/app/features/actions/actions-page'
import { AppearancePage } from '@/app/features/appearance/appearance-page'
import { ScenePage } from '@/app/features/scene/scene-page'
import { $companionSection, setCompanionSection } from '@/app/navigation/app-navigation'
import { $lifeRevision } from '@/modules/character'
import { Activity, Brain, Home, Shirt, Users, Volume2 } from '@/shared/lib/icons'
import { requestResource } from '@/shared/lib/resource-request'
import { SettingsSectionIntro } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { MemorySection } from './memory-section'
import { PersonaPage } from './persona-page'
import { VoicePage } from './voice-page'

const ENTRIES = [
  { id: 'profile', icon: Users },
  { id: 'voice', icon: Volume2 },
  { id: 'memory', icon: Brain },
  { id: 'wardrobe', icon: Shirt },
  { id: 'environment', icon: Home },
  { id: 'actions', icon: Activity }
] as const

export function CompanionHub(): React.JSX.Element {
  const auth = useStore($auth)
  const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind

  return <CompanionSession key={identity} />
}

function CompanionSession(): React.JSX.Element {
  const section = useStore($companionSection)
  const lifeRevision = useStore($lifeRevision)
  const strings = useStrings()
  const persona = strings.settings.persona

  const boundedFeature = section !== 'profile'

  const page = (): React.ReactNode => {
    switch (section) {
      case 'profile':
        return (
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <LifeStatusCard revision={lifeRevision} strings={persona.lifeStatus} />
            <PersonaPage />
          </div>
        )

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

function LifeStatusCard({
  revision,
  strings
}: {
  revision: number
  strings: { title: string; fallback: string; activePursuit: string }
}): React.JSX.Element | null {
  const [state, setState] = useState<{
    current_activity: string
    mood: string | null
    active_pursuit_id: number | null
  } | null>(null)

  useEffect(() => {
    let alive = true
    void requestResource('companion_life.get_life')
      .then(value => {
        if (alive) {
          setState(value)
        }
      })
      .catch(() => {
        if (alive) {
          setState(null)
        }
      })

    return () => {
      alive = false
    }
  }, [revision])

  if (!state) {
    return null
  }

  return (
    <section aria-label={strings.title} className="rounded-xl border border-subtle bg-fill-faint p-3">
      <div className="text-xs font-medium text-strong">{strings.title}</div>
      <p className="mt-1 text-sm text-muted">{state.current_activity || strings.fallback}</p>
      {state.mood ? <p className="mt-1 text-xs text-faint">{state.mood}</p> : null}
      {state.active_pursuit_id ? <p className="mt-1 text-xs text-faint">{strings.activePursuit}</p> : null}
    </section>
  )
}
