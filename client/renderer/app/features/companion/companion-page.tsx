import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef } from 'react'

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

import styles from './companion-page.module.css'
import { MediaReviewQueue } from './media-review-queue'
import { MemorySection } from './memory-section'
import { PersonaPage } from './persona-page'
import { VoicePage } from './voice-page'

const ENTRIES = [
  { id: 'profile', icon: Users, zh: '资料', en: 'Profile' },
  { id: 'voice', icon: Volume2, zh: '声音', en: 'Voice' },
  { id: 'appearance', icon: Shirt, zh: '衣柜', en: 'Wardrobe' },
  { id: 'scene', icon: Home, zh: '窗口背景', en: 'Window backgrounds' },
  { id: 'desktop-life', icon: Monitor, zh: '桌景', en: 'Desktop scenes' },
  { id: 'memory', icon: Brain, zh: '记忆', en: 'Memories' }
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
  const scroller = useRef<HTMLDivElement>(null)
  const positions = useRef<Partial<Record<typeof section, number>>>({})
  const boundedFeature = section === 'desktop-life' || section === 'appearance' || section === 'scene'

  useEffect(() => {
    if (route.page === 'companion' && route.companionTask === 'create') {
      void applyAppNavigation({
        page: 'companion',
        companionSection: section,
        returnToDesktopLife: route.returnToDesktopLife
      })
    }
  }, [route.page, route.companionTask, route.returnToDesktopLife, section])

  useLayoutEffect(() => {
    const element = scroller.current
    const savedPositions = positions.current

    if (element) {
      element.scrollTop = savedPositions[section] ?? 0
    }

    return () => {
      if (element) {
        savedPositions[section] = element.scrollTop
      }
    }
  }, [section])

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
          <>
            <SettingsSectionIntro
              hint={
                english
                  ? 'Each companion and assistant remembers its own conversations.'
                  : '伙伴和不同助手分别记住各自对话中的事情。你可以查看、修改或让它忘记。'
              }
              title={english ? 'Memories' : '记忆'}
            />
            <MemorySection />
          </>
        )
    }
  }

  return (
    <div className={styles.layout}>
      <div className={styles.shell}>
        <nav
          aria-label={english ? 'Companion categories' : '伙伴分类'}
          className={styles.directory}
          data-companion-navigation
        >
          {ENTRIES.map(({ id, icon: Icon, zh, en }) => (
            <button
              aria-current={section === id ? 'page' : undefined}
              className={styles.category}
              key={id}
              onClick={() => setCompanionSection(id)}
              type="button"
            >
              <Icon aria-hidden="true" size={18} />
              <span>{english ? en : zh}</span>
            </button>
          ))}
        </nav>
        <div className={styles.contentColumn}>
          <div className={styles.toolbar}>
            {route.returnToDesktopLife && (
              <button className={BTN_SUBTLE} onClick={() => returnToDesktop()} type="button">
                {english ? 'Return to desktop scene' : '返回桌景制作'}
              </button>
            )}
            <MediaReviewQueue />
          </div>
          <div className={boundedFeature ? styles.bounded : styles.scroller} ref={scroller}>
            <div className={boundedFeature ? styles.boundedContent : styles.content}>{page()}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
