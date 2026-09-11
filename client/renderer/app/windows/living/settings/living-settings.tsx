// 生活空间「设置」：顶部分区胶囊切换角色/音色/交互/外观/快捷键/关于。

import { useStore } from '@nanostores/react'
import type React from 'react'

import { PAGE_INSET_X } from '@/shared/layout/page-inset'
import { triggerHaptic } from '@/shared/lib/haptics'
import { type IconComponent, Info, Keyboard, Palette, SlidersHorizontal, Users, Volume2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { CapsuleTabs } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import {
  $livingSettingsSection,
  LIVING_SETTINGS_SECTIONS,
  type LivingSettingsSection,
  setLivingSettingsSection
} from '../living-store'

import { AboutPage } from './about-page'
import { InteractionPage } from './interaction-page'
import { PersonaPage } from './persona-page'
import { ShortcutsPage } from './shortcuts-page'
import { ThemePage } from './theme-page'
import { VoicePage } from './voice-page'

type SectionPage = () => React.JSX.Element

const SECTION_PAGES: Record<LivingSettingsSection, SectionPage> = {
  about: AboutPage,
  interaction: InteractionPage,
  persona: PersonaPage,
  shortcuts: ShortcutsPage,
  theme: ThemePage,
  voice: VoicePage
}

const SECTION_ICONS: Record<LivingSettingsSection, IconComponent> = {
  about: Info,
  interaction: SlidersHorizontal,
  persona: Users,
  shortcuts: Keyboard,
  theme: Palette,
  voice: Volume2
}

export function LivingSettings(): React.JSX.Element {
  const section = useStore($livingSettingsSection)
  const Page = SECTION_PAGES[section]
  const dict = useStrings()
  const nav = dict.settings.nav

  const sectionLabels: Record<LivingSettingsSection, string> = {
    about: nav.about,
    interaction: nav.interaction,
    persona: nav.persona,
    shortcuts: nav.shortcuts,
    theme: nav.appearance,
    voice: nav.voice
  }

  const NAV_OPTIONS: ReadonlyArray<{
    icon: IconComponent
    label: string
    value: LivingSettingsSection
  }> = LIVING_SETTINGS_SECTIONS.map(id => ({
    icon: SECTION_ICONS[id],
    label: sectionLabels[id],
    value: id
  }))

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-transparent">
      <header className="sticky top-0 z-20 flex shrink-0 justify-center px-6 pt-3.5 pb-2.5">
        <div aria-hidden="true" className="settings-header-scrim pointer-events-none absolute inset-0 -bottom-3 z-0" />
        <div className="relative z-10">
          <CapsuleTabs
            ariaLabel={nav.navAriaLabel}
            onChange={id => {
              triggerHaptic('open')
              setLivingSettingsSection(id)
            }}
            options={NAV_OPTIONS}
            value={section}
          />
        </div>
      </header>

      <div className={cn('min-h-0 flex-1 overflow-y-auto py-5', PAGE_INSET_X)} key={section}>
        <div className="mx-auto w-full max-w-3xl pb-16">
          <Page />
        </div>
      </div>
    </div>
  )
}
