import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

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
import { $persona } from '@/modules/character'
import { ArrowLeft, ArrowRight, Brain, Home, Monitor, Shirt, Users, Volume2 } from '@/shared/lib/icons'
import { BTN_SUBTLE, Disclosure, SettingsSectionIntro } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'

import { MediaReviewQueue } from './media-review-queue'
import { MemorySection } from './memory-section'
import { PersonaPage } from './persona-page'
import { VoicePage } from './voice-page'

const ENTRIES = [
  {
    id: 'profile',
    icon: Users,
    zh: '伙伴资料',
    en: 'About your companion',
    hint: '名字、性格和相处方式',
    enHint: 'Name, personality, and your relationship'
  },
  {
    id: 'voice',
    icon: Volume2,
    zh: '声音',
    en: 'Voice',
    hint: '选择音色、自主设计或克隆声音',
    enHint: 'Choose, design, or clone a voice'
  },
  {
    id: 'appearance',
    icon: Shirt,
    zh: '衣柜',
    en: 'Wardrobe',
    hint: '挑选穿搭，设计新的造型',
    enHint: 'Choose an outfit or create a new look'
  },
  {
    id: 'scene',
    icon: Home,
    zh: '窗口背景',
    en: 'Window backgrounds',
    hint: '为聊天窗口换一个喜欢的地方',
    enHint: 'Choose a place for your conversations'
  },
  {
    id: 'desktop-life',
    icon: Monitor,
    zh: '桌景',
    en: 'Desktop scenes',
    hint: '让伙伴在电脑桌面中陪着你',
    enHint: 'Bring your companion into your desktop'
  },
  {
    id: 'memory',
    icon: Brain,
    zh: '记忆',
    en: 'Memories',
    hint: '查看、修改或忘记聊天中记住的事情',
    enHint: 'Review, edit, or forget what was remembered'
  }
] as const

export function CompanionHub(): React.JSX.Element {
  const section = useStore($companionSection)
  const english = useStore($locale) === 'en'
  const persona = useStore($persona)
  const route = useStore($appNavigation)
  useEffect(() => {
    if (route.page === 'companion' && route.companionTask === 'create') {
      void applyAppNavigation({
        page: 'companion',
        companionSection: section,
        returnToDesktopLife: route.returnToDesktopLife
      })
    }
  }, [route.page, route.companionTask, route.returnToDesktopLife, section])
  const title = ENTRIES.find(item => item.id === section)
  const boundedFeature = section === 'desktop-life' || section === 'appearance' || section === 'scene'

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
        ...current,
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

      case 'home':
        return null
    }
  }

  return (
    <div
      className={
        boundedFeature
          ? 'flex h-full min-h-0 flex-col overflow-hidden px-5 pt-5 sm:px-7 sm:pt-7'
          : 'h-full min-h-0 overflow-y-auto p-5 sm:p-7'
      }
    >
      <div
        className={
          boundedFeature
            ? 'mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col gap-5'
            : 'mx-auto w-full max-w-5xl space-y-5'
        }
      >
        {section === 'home' ? (
          <>
            <SettingsSectionIntro
              hint={
                english
                  ? 'Make this companion feel at home with you.'
                  : '从日常穿搭到生活空间，把伙伴变成你喜欢的样子。'
              }
              title={persona?.name || (english ? 'Your companion' : '你的伙伴')}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              {ENTRIES.map(({ id, icon: Icon, zh, en, hint, enHint }) => (
                <button
                  className="flex min-h-28 items-center gap-4 rounded-2xl border border-line-hairline bg-surface-card p-5 text-left transition hover:bg-fill-hover focus-visible:outline-2 focus-visible:outline-accent"
                  key={id}
                  onClick={() => setCompanionSection(id)}
                  type="button"
                >
                  <span className="rounded-xl bg-accent-soft p-3 text-accent">
                    <Icon aria-hidden="true" size={23} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-base font-medium text-strong">{english ? en : zh}</span>
                    <span className="mt-1.5 block text-sm leading-relaxed text-muted">{english ? enHint : hint}</span>
                  </span>
                  <ArrowRight aria-hidden="true" className="shrink-0 text-muted" size={17} />
                </button>
              ))}
            </div>
            <Disclosure
              hint={
                english
                  ? 'Review new results before replacing an existing appearance.'
                  : '查看新制作的结果，再决定是否替换现有形象。'
              }
              title={english ? 'Items awaiting your review' : '需要你确认的内容'}
            >
              <MediaReviewQueue />
            </Disclosure>
          </>
        ) : (
          <>
            <div className="flex shrink-0 flex-wrap items-center gap-3">
              <button className={BTN_SUBTLE} onClick={() => setCompanionSection('home')} type="button">
                <ArrowLeft size={15} />
                {english ? 'Companion' : '伙伴'}
              </button>
              <span className="text-sm text-muted">{english ? title?.en : title?.zh}</span>
              {route.returnToDesktopLife && (
                <button className={`${BTN_SUBTLE} ml-auto`} onClick={() => returnToDesktop()} type="button">
                  {english ? 'Continue desktop scene' : '返回桌景制作'}
                </button>
              )}
            </div>
            {page()}
          </>
        )}
      </div>
    </div>
  )
}
