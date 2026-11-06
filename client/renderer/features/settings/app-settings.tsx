import { useStore } from '@nanostores/react'
import { IconDeviceMobile } from '@tabler/icons-react'
import type React from 'react'

import { $settingsSection, $settingsTab, openSettings, SETTINGS_SECTIONS } from '@/app/navigation/app-navigation'
import {
  Brain,
  Command,
  Globe,
  type IconComponent,
  Info,
  Keyboard,
  Palette,
  SlidersHorizontal,
  Sparkles
} from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'
import type { AppPageProps } from '@/shared/types/app-page'
import { CapsuleTabs, Disclosure, SettingsSectionIntro } from '@/ui'
import { SectionShell } from '@/ui/section-shell/section-shell'
import type { SettingsSection, SettingsTab } from '@ipc/contracts'

import { AboutPage } from './about-page'
import { ApplicationSettings } from './application-settings'
import { CompanionPage } from './companion-page'
import { InteractionPage } from './interaction-page'
import { PresentationPage } from './presentation-page'
import { RemotePage } from './remote/remote-page'
import { ShortcutsPage } from './shortcuts-page'
import { SocialAccountsSection } from './social-accounts-section'
import { InferencePage } from './station/inference-page'
import { RunnerPage } from './station/runner-page'
import { SkillsPage } from './station/skills-page'
import { ToolsetsPage } from './station/toolsets-page'
import { ThemePage } from './theme-page'

const ICONS: Record<SettingsSection, IconComponent> = {
  companionship: SlidersHorizontal,
  appearance: Palette,
  sources: Globe,
  devices: IconDeviceMobile,
  capabilities: Sparkles,
  advanced: Brain
}

const DEFAULT_TAB: Partial<Record<SettingsSection, SettingsTab>> = {
  companionship: 'together',
  capabilities: 'skills',
  advanced: 'general'
}

/** 设置：常用项在前，进阶配置集中在最后一组；页签只在本组内切换。 */
export function AppSettings(_props: AppPageProps): React.JSX.Element {
  const section = useStore($settingsSection)
  const tab = useStore($settingsTab)
  const strings = useStrings()
  const labels = strings.settings.application.sections
  const activeTab = tab ?? DEFAULT_TAB[section]

  const tabOptions = (): ReadonlyArray<{ value: SettingsTab; label: string; icon?: IconComponent }> => {
    switch (section) {
      case 'companionship':
        return [
          { value: 'together', label: strings.settings.interaction.title, icon: SlidersHorizontal },
          { value: 'voice', label: strings.settings.interaction.voiceHeading, icon: Sparkles }
        ]

      case 'capabilities':
        return [
          { value: 'skills', label: strings.skills.tabSkills, icon: Sparkles },
          { value: 'toolsets', label: strings.skills.tabToolsets, icon: Command }
        ]

      case 'advanced':
        return [
          { value: 'general', label: strings.settings.application.generalTitle, icon: SlidersHorizontal },
          { value: 'shortcuts', label: strings.settings.application.shortcutsTitle, icon: Keyboard },
          { value: 'about', label: strings.settings.nav.about, icon: Info }
        ]

      default:
        return []
    }
  }

  const options = tabOptions()

  const content = (): React.ReactNode => {
    switch (section) {
      case 'companionship':
        return <InteractionPage part={activeTab === 'voice' ? 'voice' : 'companionship'} />

      case 'appearance':
        return (
          <div className="space-y-6">
            <SettingsSectionIntro hint={strings.settings.application.displayHint} title={labels.appearance} />
            <ThemePage />
            <PresentationPage />
            <CompanionPage />
          </div>
        )

      case 'sources':
        return <SocialAccountsSection />

      case 'devices':
        return <RemotePage />

      case 'capabilities':
        return activeTab === 'toolsets' ? <ToolsetsPage /> : <SkillsPage />

      case 'advanced':
        return activeTab === 'shortcuts' ? (
          <ShortcutsPage />
        ) : activeTab === 'about' ? (
          <AboutPage />
        ) : (
          <div className="space-y-5">
            <SettingsSectionIntro hint={strings.settings.application.advancedHint} title={labels.advanced} />
            <ApplicationSettings />
            <Disclosure
              hint={strings.settings.application.conversationHint}
              title={strings.settings.application.conversationTitle}
            >
              <InferencePage />
            </Disclosure>
            <Disclosure
              hint={strings.settings.application.executionHint}
              title={strings.settings.application.executionTitle}
            >
              <RunnerPage />
            </Disclosure>
          </div>
        )
    }
  }

  return (
    <SectionShell
      activeId={section}
      ariaLabel={strings.settings.application.categoriesAria}
      entries={SETTINGS_SECTIONS.map(id => ({ id, icon: ICONS[id], label: labels[id] }))}
      onSelect={next => void openSettings(next)}
      tabs={
        options.length > 0 ? (
          <CapsuleTabs<SettingsTab>
            ariaLabel={labels[section]}
            onChange={next => void openSettings(section, next)}
            options={options}
            value={activeTab ?? options[0].value}
          />
        ) : undefined
      }
    >
      <div key={`${section}:${activeTab ?? ''}`}>{content()}</div>
    </SectionShell>
  )
}
