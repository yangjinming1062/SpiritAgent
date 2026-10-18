import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

import { DirectoryShell } from '@/app/components/directory-shell/directory-shell'
import { AppearancePage } from '@/app/features/appearance/appearance-page'
import { DesktopLifePage } from '@/app/features/desktop-life/desktop-life-page'
import { $desktopLifeUi, updateDesktopLifeUi } from '@/app/features/desktop-life/desktop-life-state'
import { ScenePage } from '@/app/features/scene/scene-page'
import {
  $appNavigation,
  $companionSection,
  applyAppNavigation,
  setCompanionSection
} from '@/app/navigation/app-navigation'
import { Brain, Home, Monitor, Shirt, Users, Volume2 } from '@/shared/lib/icons'
import { BTN_SUBTLE, SettingsSectionIntro } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $locale } from '@/shared/store/locale'

import { MediaReviewQueue } from './media-review-queue'
import { MemorySection } from './memory-section'
import { PersonaPage } from './persona-page'
import { VoicePage } from './voice-page'

const ENTRIES = [
  { id: 'profile', icon: Users, zh: '资料', en: 'Profile' },
  { id: 'voice', icon: Volume2, zh: '声音', en: 'Voice' },
  { id: 'appearance', icon: Shirt, zh: '衣柜', en: 'Wardrobe' },
  { id: 'memory', icon: Brain, zh: '记忆', en: 'Memories' },
  { id: 'scene', icon: Home, zh: '窗口背景', en: 'Window backgrounds' },
  { id: 'desktop-life', icon: Monitor, zh: '桌面背景', en: 'Desktop backgrounds' }
] as const

export function CompanionHub(): React.JSX.Element {
  const auth = useStore($auth)
  const identity = auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind

  return <CompanionSession key={identity} />
}

function CompanionSession(): React.JSX.Element {
  const section = useStore($companionSection)
  const english = useStore($locale) === 'en'
  const route = useStore($appNavigation)

  const boundedFeature =
    section === 'desktop-life' || section === 'appearance' || section === 'scene' || section === 'memory'

  useEffect(() => {
    if (route.page === 'companion' && route.companionTask === 'create') {
      void applyAppNavigation({
        page: 'companion',
        companionSection: section,
        returnToDesktopLife: route.returnToDesktopLife
      })
    }
  }, [route.page, route.companionTask, route.returnToDesktopLife, section])

  const prepareMaterial = (kind: 'scene' | 'outfit'): void => {
    void applyAppNavigation({
      page: 'companion',
      companionSection: kind === 'scene' ? 'scene' : 'appearance',
      companionTask: 'create',
      returnToDesktopLife: true
    })
  }

  const returnToDesktop = (id?: number | string): void => {
    if (id !== undefined) {
      const current = $desktopLifeUi.get()
      updateDesktopLifeUi({
        create: {
          ...current.create,
          ...(section === 'scene' ? { sceneId: String(id), sceneLabel: '' } : { outfitId: String(id) }),
          requestId: crypto.randomUUID()
        }
      })
    }

    setCompanionSection('desktop-life')
  }

  const page = (): React.ReactNode => {
    switch (section) {
      case 'profile':
        return <PersonaPage />

      case 'voice':
        return <VoicePage />

      case 'appearance':
        return (
          <AppearancePage
            onMaterialReady={route.returnToDesktopLife ? returnToDesktop : undefined}
            startCreating={route.companionTask === 'create'}
          />
        )

      case 'scene':
        return (
          <ScenePage
            onMaterialReady={route.returnToDesktopLife ? returnToDesktop : undefined}
            startCreating={route.companionTask === 'create'}
          />
        )

      case 'desktop-life':
        return <DesktopLifePage onPrepareMaterial={prepareMaterial} />

      case 'memory':
        return (
          <div className="flex min-h-0 flex-1 flex-col pb-4">
            <SettingsSectionIntro
              hint={
                english
                  ? 'Each companion and assistant remembers its own conversations.'
                  : '伙伴和不同助手分别记住各自对话中的事情。你可以查看、修改或让它忘记。'
              }
              title={english ? 'Memories' : '记忆'}
            />
            <MemorySection />
          </div>
        )
    }
  }

  return (
    <DirectoryShell
      activeId={section}
      ariaLabel={english ? 'Companion categories' : '伙伴分类'}
      bounded={boundedFeature}
      entries={ENTRIES.map(({ id, icon, zh, en }) => ({ id, icon, label: english ? en : zh }))}
      onSelect={setCompanionSection}
      toolbar={
        <>
          {route.returnToDesktopLife && (
            <button className={BTN_SUBTLE} onClick={() => returnToDesktop()} type="button">
              {english ? 'Return to desktop background' : '返回桌面背景制作'}
            </button>
          )}
          <MediaReviewQueue />
        </>
      }
    >
      {page()}
    </DirectoryShell>
  )
}
