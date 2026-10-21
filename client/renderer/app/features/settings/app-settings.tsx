import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { AccountMenu } from '@/app/features/accounts/account-menu'
import { RemotePage } from '@/app/features/remote/remote-page'
import {
  $appNavigationTarget,
  $settingsSection,
  setSettingsSection,
  SETTINGS_SECTIONS
} from '@/app/navigation/app-navigation'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Brain, Globe, type IconComponent, Info, Monitor, SlidersHorizontal, Volume2 } from '@/shared/lib/icons'
import { BTN_SUBTLE, Disclosure, PanelSelect, SettingCard, SettingRow, SettingsSectionIntro } from '@/shared/panel'
import { runnerApi } from '@/shared/platform/desktop'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { SettingsSection } from '@ipc/contracts'

import { AboutPage } from './about-page'
import styles from './app-settings.module.css'
import { CompanionPage } from './companion-page'
import { InteractionPage } from './interaction-page'
import { PresentationPage } from './presentation-page'
import { ShortcutsPage } from './shortcuts-page'
import { InferencePage } from './station/inference-page'
import { RunnerPage } from './station/runner-page'
import { SkillsToolsTabs } from './station/skills-tools-tabs'
import { ThemePage } from './theme-page'

const ICONS: Record<SettingsSection, IconComponent> = {
  companionship: SlidersHorizontal,
  voice: Volume2,
  display: Monitor,
  devices: Globe,
  application: Info,
  advanced: Brain
}

function ApplicationSettings(): React.JSX.Element {
  const locale = useStore($locale)
  const strings = useStrings()
  const [accountOpen, setAccountOpen] = useState(false)
  const [savingLanguage, setSavingLanguage] = useState(false)
  const accountRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const begin = useAsyncGuard()

  const closeAccounts = (): void => {
    setAccountOpen(false)
    buttonRef.current?.focus()
  }

  useDismissOnOutside(accountRef, accountOpen, closeAccounts)

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
        <SettingRow
          description={strings.settings.application.accountsDescription}
          label={strings.settings.application.accountsLabel}
        >
          <div className="relative" ref={accountRef}>
            <button
              aria-expanded={accountOpen}
              aria-haspopup="menu"
              className={BTN_SUBTLE}
              onClick={() => setAccountOpen(open => !open)}
              ref={buttonRef}
              type="button"
            >
              {strings.settings.application.manageAccounts}
            </button>
            {accountOpen && <AccountMenu onClose={closeAccounts} />}
          </div>
        </SettingRow>
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
      <Disclosure title={strings.settings.application.shortcutsTitle}>
        <ShortcutsPage />
      </Disclosure>
      <AboutPage />
    </div>
  )
}

export function AppSettings(): React.JSX.Element {
  const section = useStore($settingsSection)
  const target = useStore($appNavigationTarget)
  const [abilitiesOpen, setAbilitiesOpen] = useState(false)
  const strings = useStrings()
  const labels = strings.settings.application.sections

  useEffect(() => {
    if (target.page === 'settings' && target.section === 'devices' && target.settingsTab) {
      setAbilitiesOpen(true)
    }
  }, [target])

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

      case 'display':
        return (
          <div className="space-y-5">
            <ThemePage />
            <Disclosure title={strings.settings.application.companionSizeTitle}>
              <CompanionPage />
            </Disclosure>
            <Disclosure title={strings.settings.application.windowModeTitle}>
              <PresentationPage />
            </Disclosure>
          </div>
        )

      case 'devices':
        return (
          <div className="space-y-5">
            <RemotePage />
            <Disclosure
              hint={strings.settings.application.abilitiesHint}
              onOpenChange={setAbilitiesOpen}
              open={abilitiesOpen}
              title={strings.settings.application.abilitiesTitle}
            >
              <SkillsToolsTabs />
            </Disclosure>
          </div>
        )

      case 'application':
        return <ApplicationSettings />

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
        <div className={styles.scroller} key={section}>
          <div className={styles.content} data-section={section}>
            {content()}
          </div>
        </div>
      </div>
    </div>
  )
}
