// 桌面设置只装配共享生活分区与工位分区，窗口伙伴布局由生活空间设置管理。

import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $livingSettingsSection,
  LIVING_SETTINGS_SECTIONS,
  type LivingSettingsSection,
  setLivingSettingsSection
} from '@/app/features/living/living-store'
import { AboutPage } from '@/app/features/living/settings/about-page'
import { InteractionPage } from '@/app/features/living/settings/interaction-page'
import { PersonaPage } from '@/app/features/living/settings/persona-page'
import { ShortcutsPage } from '@/app/features/living/settings/shortcuts-page'
import { ThemePage } from '@/app/features/living/settings/theme-page'
import { VoicePage } from '@/app/features/living/settings/voice-page'
import { InferencePage } from '@/app/features/workbench/station/inference-page'
import { RunnerPage } from '@/app/features/workbench/station/runner-page'
import { SkillsToolsTabs } from '@/app/features/workbench/station/skills-tools-tabs'
import { PAGE_INSET_X } from '@/shared/layout/page-inset'
import { triggerHaptic } from '@/shared/lib/haptics'
import { normalizeHashPath } from '@/shared/lib/hash-route'
import {
  Brain,
  Cpu,
  type IconComponent,
  Info,
  Keyboard,
  Palette,
  SlidersHorizontal,
  Sparkles,
  Users,
  Volume2
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { CapsuleTabs } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

type StationSection = 'inference' | 'runner' | 'skills'
type DesktopLivingSection = Exclude<LivingSettingsSection, 'companion'>

type DesktopSettingsSection = DesktopLivingSection | StationSection

const STATION_SECTIONS: ReadonlyArray<StationSection> = ['inference', 'runner', 'skills']
const DESKTOP_LIVING_SECTIONS = LIVING_SETTINGS_SECTIONS.filter(id => id !== 'companion')

const SECTION_PAGES: Record<DesktopSettingsSection, () => React.JSX.Element> = {
  about: AboutPage,
  inference: InferencePage,
  interaction: InteractionPage,
  persona: PersonaPage,
  runner: RunnerPage,
  shortcuts: ShortcutsPage,
  skills: SkillsToolsTabs,
  theme: ThemePage,
  voice: VoicePage
}

const SECTION_ICONS: Record<DesktopSettingsSection, IconComponent> = {
  about: Info,
  inference: Brain,
  interaction: SlidersHorizontal,
  persona: Users,
  runner: Cpu,
  shortcuts: Keyboard,
  skills: Sparkles,
  theme: Palette,
  voice: Volume2
}

// 共享生活分区保持既有次序，工位分区随后；「关于」压轴。
const SECTION_ORDER: ReadonlyArray<DesktopSettingsSection> = [
  ...DESKTOP_LIVING_SECTIONS.filter(id => id !== 'about'),
  ...STATION_SECTIONS,
  'about'
]

function isStationSection(section: DesktopSettingsSection): section is StationSection {
  return (STATION_SECTIONS as readonly string[]).includes(section)
}

function fallbackLivingSection(): DesktopLivingSection {
  const current = $livingSettingsSection.get()

  return current === 'companion' ? 'persona' : current
}

// 深链解析：`#/settings/<section>` 指向生活分区，`#/inference|runner|skills` 与 `#/station/…` 指向工位分区；无深链时沿用生活分区的持久化状态。
function readHashSection(): DesktopSettingsSection {
  const segments = normalizeHashPath(window.location.hash).split('/')
  const [first, second] = segments

  if (first === 'settings') {
    return (DESKTOP_LIVING_SECTIONS as readonly string[]).includes(second)
      ? (second as DesktopLivingSection)
      : fallbackLivingSection()
  }

  if (first === 'station') {
    return (STATION_SECTIONS as readonly string[]).includes(second) ? (second as StationSection) : 'inference'
  }

  return (STATION_SECTIONS as readonly string[]).includes(first) ? (first as StationSection) : fallbackLivingSection()
}

export function DesktopPreferences(): React.JSX.Element {
  const dict = useStrings()
  const [section, setSection] = useState<DesktopSettingsSection>(readHashSection)
  const nav = dict.settings.nav
  const stationTabs = dict.workbench.station.tabs
  const Page = SECTION_PAGES[section]

  const applySection = (next: DesktopSettingsSection): void => {
    setSection(next)

    if (isStationSection(next)) {
      window.history.replaceState(null, '', `#/${next}`)
    } else {
      setLivingSettingsSection(next)
    }
  }

  // 设置窗口内 hash 变化时同步分区（深链入站 + 打开期间被再次定位）。
  useEffect(() => {
    const onHash = (): void => applySection(readHashSection())

    window.addEventListener('hashchange', onHash)

    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleSelect = (next: DesktopSettingsSection): void => {
    triggerHaptic('open')
    applySection(next)
  }

  const options = SECTION_ORDER.map(id => ({
    icon: SECTION_ICONS[id],
    label: isStationSection(id) ? stationTabs[id] : nav[id],
    value: id
  }))

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-transparent">
      <header className="sticky top-0 z-20 flex shrink-0 justify-center px-6 pt-3.5 pb-2.5">
        <div aria-hidden="true" className="settings-header-scrim pointer-events-none absolute inset-0 -bottom-3 z-0" />
        <div className="relative z-10 flex items-center gap-3">
          <CapsuleTabs
            ariaLabel={nav.navAriaLabel}
            onChange={handleSelect}
            options={options}
            size="sm"
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
