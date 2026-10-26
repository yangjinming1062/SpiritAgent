import { useStore } from '@nanostores/react'
import { IconDeviceMobile } from '@tabler/icons-react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { RemotePage } from '@/app/features/remote/remote-page'
import {
  $appNavigationTarget,
  $settingsSection,
  applyAppNavigation,
  setSettingsSection,
  SETTINGS_SECTIONS
} from '@/app/navigation/app-navigation'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { triggerHaptic } from '@/shared/lib/haptics'
import {
  Brain,
  Command,
  Globe,
  type IconComponent,
  Info,
  Keyboard,
  Monitor,
  Palette,
  SlidersHorizontal,
  Sparkles,
  Volume2
} from '@/shared/lib/icons'
import { CapsuleTabs, Disclosure, PanelSelect, SettingCard, SettingRow, SettingsSectionIntro } from '@/shared/panel'
import { runnerApi } from '@/shared/platform/desktop'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { SettingsSection, SettingsTab } from '@ipc/contracts'

import { AboutPage } from './about-page'
import styles from './app-settings.module.css'
import { CompanionPage } from './companion-page'
import { InteractionPage } from './interaction-page'
import { PresentationPage } from './presentation-page'
import { ShortcutsPage } from './shortcuts-page'
import { SocialAccountsSection } from './social-accounts-section'
import { InferencePage } from './station/inference-page'
import { RunnerPage } from './station/runner-page'
import { SkillsPage } from './station/skills-page'
import { ToolsetsPage } from './station/toolsets-page'
import { ThemePage } from './theme-page'

const ICONS: Record<SettingsSection, IconComponent> = {
  companionship: SlidersHorizontal,
  voice: Volume2,
  sources: Globe,
  theme: Palette,
  display: Monitor,
  devices: IconDeviceMobile,
  capabilities: Sparkles,
  application: Info,
  advanced: Brain
}

function ApplicationSettings(): React.JSX.Element {
  const locale = useStore($locale)
  const strings = useStrings()
  const [savingLanguage, setSavingLanguage] = useState(false)
  const begin = useAsyncGuard()

  const changeLanguage = async (value: string): Promise<void> => {
    const live = begin()
    setSavingLanguage(true)

    try {
      const result = await runnerApi().runnerConfig.patch({ path: ['language'], value })

      if (!result.ok) {
        throw new Error(result.error)
      }
    } catch (error) {
      if (live()) {
        notifyError(error, strings.settings.application.languageSaveFailed)
      }
    } finally {
      if (live()) {
        setSavingLanguage(false)
      }
    }
  }

  return (
    <div className="space-y-5">
      <SettingsSectionIntro hint={strings.settings.application.hint} title={strings.settings.application.title} />
      <SettingCard>
        <SettingRow label={strings.settings.application.languageLabel}>
          <PanelSelect
            ariaLabel={strings.settings.application.languageLabel}
            disabled={savingLanguage}
            onChange={value => void changeLanguage(value)}
            options={[
              { value: 'zh', label: '简体中文' },
              { value: 'en', label: 'English' }
            ]}
            value={locale}
          />
        </SettingRow>
      </SettingCard>
    </div>
  )
}

export function AppSettings(): React.JSX.Element {
  const section = useStore($settingsSection)
  const target = useStore($appNavigationTarget)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const settingsTab = target.settingsTab ?? (section === 'capabilities' ? 'skills' : 'general')
  const strings = useStrings()
  const labels = strings.settings.application.sections

  useEffect(() => {
    scrollerRef.current?.scrollTo(0, 0)
  }, [settingsTab])

  const selectTab = (next: SettingsTab): void => {
    if (next !== settingsTab) {
      triggerHaptic()
      void applyAppNavigation({
        page: 'settings',
        section,
        settingsTab: next
      })
    }
  }

  const select = (next: SettingsSection): void => {
    triggerHaptic('open')
    setSettingsSection(next)
  }

  const content = (): React.ReactNode => {
    switch (section) {
      case 'companionship':
        return <InteractionPage part="companionship" />

      case 'voice':
        return <InteractionPage part="voice" />

      case 'sources':
        return <SocialAccountsSection />

      case 'theme':
        return <ThemePage />

      case 'display':
        return (
          <div className="space-y-6">
            <SettingsSectionIntro hint={strings.settings.application.displayHint} title={labels.display} />
            <PresentationPage />
            <CompanionPage />
          </div>
        )

      case 'devices':
        return <RemotePage />

      case 'capabilities':
        return settingsTab === 'toolsets' ? <ToolsetsPage /> : <SkillsPage />

      case 'application':
        return settingsTab === 'shortcuts' ? (
          <ShortcutsPage />
        ) : settingsTab === 'about' ? (
          <AboutPage />
        ) : (
          <ApplicationSettings />
        )

      case 'advanced':
        return (
          <div className="space-y-5">
            <SettingsSectionIntro hint={strings.settings.application.advancedHint} title={labels.advanced} />
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
    <div className={styles.layout}>
      <nav aria-label={strings.settings.application.categoriesAria} className={styles.directory}>
        {SETTINGS_SECTIONS.map(id => {
          const Icon = ICONS[id]

          return (
            <button
              aria-current={section === id ? 'page' : undefined}
              className={`${styles.category} ${id === 'advanced' ? styles.advanced : ''}`}
              key={id}
              onClick={() => select(id)}
              type="button"
            >
              <Icon aria-hidden="true" size={17} />
              <span>{labels[id]}</span>
            </button>
          )
        })}
      </nav>
      <div className={styles.contentColumn}>
        <div className={styles.mobileDirectory}>
          <span>{strings.settings.application.settingsLabel}</span>
          <PanelSelect
            ariaLabel={strings.settings.application.categoryAria}
            onChange={select}
            options={SETTINGS_SECTIONS.map(id => ({ value: id, label: labels[id] }))}
            value={section}
            widthClass="w-full"
          />
        </div>
        {(section === 'capabilities' || section === 'application') && (
          <div className={styles.tabs}>
            <div className={styles.content}>
              <CapsuleTabs<SettingsTab>
                ariaLabel={labels[section]}
                onChange={selectTab}
                options={
                  section === 'capabilities'
                    ? [
                        { value: 'skills', label: strings.skills.tabSkills, icon: Sparkles },
                        { value: 'toolsets', label: strings.skills.tabToolsets, icon: Command }
                      ]
                    : [
                        { value: 'general', label: strings.settings.application.generalTitle, icon: SlidersHorizontal },
                        { value: 'shortcuts', label: strings.settings.application.shortcutsTitle, icon: Keyboard },
                        { value: 'about', label: strings.settings.nav.about, icon: Info }
                      ]
                }
                value={settingsTab}
              />
            </div>
          </div>
        )}
        <div className={styles.scroller} key={section} ref={scrollerRef}>
          <div className={styles.content} data-section={section}>
            {content()}
          </div>
        </div>
      </div>
    </div>
  )
}
