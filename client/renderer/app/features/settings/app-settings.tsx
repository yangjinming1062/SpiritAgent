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
import { VoicePage } from './voice-page'

const LABELS = {
  zh: {
    companionship: '陪伴方式',
    voice: '声音与回应',
    display: '显示与外观',
    devices: '设备与能力',
    application: '应用与账户',
    advanced: '高级设置'
  },
  en: {
    companionship: 'Companionship',
    voice: 'Voice and replies',
    display: 'Display and appearance',
    devices: 'Devices and abilities',
    application: 'App and accounts',
    advanced: 'Advanced settings'
  }
} satisfies Record<'zh' | 'en', Record<SettingsSection, string>>

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
  const english = locale === 'en'
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
        notifyError(error, english ? 'Language could not be saved' : '语言未能保存，请重试')
      }
    } finally {
      if (live()) {
        setSavingLanguage(false)
      }
    }
  }

  return (
    <div className="space-y-5">
      <SettingsSectionIntro
        hint={english ? 'Manage accounts, language, shortcuts, and updates.' : '管理账户、语言、快捷键和应用更新。'}
        title={english ? 'App and accounts' : '应用与账户'}
      />
      <SettingCard>
        <SettingRow
          description={english ? 'Switch companions or add another account.' : '切换账户，或添加另一个账户。'}
          label={english ? 'Accounts' : '账户'}
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
              {english ? 'Manage accounts' : '管理账户'}
            </button>
            {accountOpen && <AccountMenu onClose={closeAccounts} />}
          </div>
        </SettingRow>
        <SettingRow label={english ? 'Language' : '界面语言'}>
          <PanelSelect
            ariaLabel={english ? 'Language' : '界面语言'}
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
      <Disclosure title={english ? 'Keyboard shortcuts' : '快捷键'}>
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
  const english = useStore($locale) === 'en'
  const labels = LABELS[english ? 'en' : 'zh']

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
        return (
          <div className="space-y-5">
            <VoicePage />
            <Disclosure title={english ? 'Replies and recording' : '回应方式与录音'}>
              <InteractionPage part="voice" />
            </Disclosure>
          </div>
        )

      case 'display':
        return (
          <div className="space-y-5">
            <ThemePage />
            <Disclosure title={english ? 'Companion size and position' : '伙伴大小与位置'}>
              <CompanionPage />
            </Disclosure>
            <Disclosure title={english ? 'Window and desktop mode' : '窗口与桌面模式'}>
              <PresentationPage />
            </Disclosure>
          </div>
        )

      case 'devices':
        return (
          <div className="space-y-5">
            <RemotePage />
            <Disclosure
              hint={
                english
                  ? 'Choose the skills and tools available on this computer.'
                  : '按用途选择这台电脑上可以使用的技能和工具。'
              }
              onOpenChange={setAbilitiesOpen}
              open={abilitiesOpen}
              title={english ? 'What your companion can do' : '伙伴能帮你做什么'}
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
            <SettingsSectionIntro
              hint={
                english
                  ? 'The recommended defaults work for everyday use. Adjust these options only when you need to.'
                  : '日常使用保持默认值即可，需要时再调整以下选项。'
              }
              title={labels.advanced}
            />
            <Disclosure
              hint={english ? 'Reasoning, randomness, and long conversations.' : '思考深度、表达随机性和长对话处理。'}
              title={english ? 'Conversation parameters' : '对话参数'}
            >
              <InferencePage />
            </Disclosure>
            <Disclosure
              hint={
                english
                  ? 'Remote terminals, network access, and sensitive information.'
                  : '远程终端、网络访问和敏感信息处理。'
              }
              title={english ? 'Computer execution settings' : '电脑执行设置'}
            >
              <RunnerPage />
            </Disclosure>
          </div>
        )
    }
  }

  return (
    <div className={styles.layout}>
      <nav aria-label={english ? 'Settings categories' : '设置分类'} className={styles.directory}>
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
          <span>{english ? 'Settings' : '设置'}</span>
          <PanelSelect
            ariaLabel={english ? 'Settings category' : '设置分类'}
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
