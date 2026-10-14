import { useStore } from '@nanostores/react'
import type React from 'react'

import { RemotePage } from '@/app/features/remote/remote-page'
import { $settingsSection, setSettingsSection, SETTINGS_SECTIONS } from '@/app/navigation/app-navigation'
import { triggerHaptic } from '@/shared/lib/haptics'
import {
  Brain,
  Cpu,
  Globe,
  type IconComponent,
  Info,
  Keyboard,
  Monitor,
  Palette,
  SlidersHorizontal,
  Sparkles,
  Users,
  Volume2
} from '@/shared/lib/icons'
import { PanelSelect } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import type { SettingsSection } from '@ipc/contracts'

import { AboutPage } from './about-page'
import styles from './app-settings.module.css'
import { CompanionPage } from './companion-page'
import { InteractionPage } from './interaction-page'
import { PersonaPage } from './persona-page'
import { PresentationPage } from './presentation-page'
import { ShortcutsPage } from './shortcuts-page'
import { InferencePage } from './station/inference-page'
import { RunnerPage } from './station/runner-page'
import { SkillsToolsTabs } from './station/skills-tools-tabs'
import { ThemePage } from './theme-page'
import { VoicePage } from './voice-page'

const SECTION_PAGES: Record<SettingsSection, React.ComponentType> = {
  persona: PersonaPage,
  voice: VoicePage,
  interaction: InteractionPage,
  theme: ThemePage,
  companion: CompanionPage,
  presentation: PresentationPage,
  inference: InferencePage,
  runner: RunnerPage,
  skills: SkillsToolsTabs,
  remote: RemotePage,
  shortcuts: ShortcutsPage,
  about: AboutPage
}

const SECTION_ICONS: Record<SettingsSection, IconComponent> = {
  persona: Users,
  voice: Volume2,
  interaction: SlidersHorizontal,
  theme: Palette,
  companion: Users,
  presentation: Monitor,
  inference: Brain,
  runner: Cpu,
  skills: Sparkles,
  remote: Globe,
  shortcuts: Keyboard,
  about: Info
}

const GROUPS: ReadonlyArray<{ zh: string; en: string; sections: readonly SettingsSection[] }> = [
  { zh: '伙伴', en: 'Companion', sections: ['persona', 'voice', 'interaction'] },
  { zh: '外观与呈现', en: 'Appearance and presentation', sections: ['theme', 'companion', 'presentation'] },
  { zh: '对话与能力', en: 'Chat and capabilities', sections: ['inference', 'runner', 'skills'] },
  { zh: '连接与应用', en: 'Connections and application', sections: ['remote', 'shortcuts', 'about'] }
]

const zh: Record<SettingsSection, string> = {
  persona: '人设与记忆',
  voice: '声音',
  interaction: '互动',
  theme: '主题',
  companion: '伙伴显示',
  presentation: '桌面模式与生活视频',
  inference: '对话与推理',
  runner: '本机执行',
  skills: '技能与工具',
  remote: '手机访问',
  shortcuts: '快捷键',
  about: '关于与更新'
}

const en: Record<SettingsSection, string> = {
  persona: 'Persona and memory',
  voice: 'Voice',
  interaction: 'Interaction',
  theme: 'Theme',
  companion: 'Companion display',
  presentation: 'Desktop mode and life videos',
  inference: 'Chat and reasoning',
  runner: 'Local execution',
  skills: 'Skills and tools',
  remote: 'Mobile access',
  shortcuts: 'Shortcuts',
  about: 'About and updates'
}

export function AppSettings(): React.JSX.Element {
  const section = useStore($settingsSection)
  const english = useStore($locale) === 'en'
  const labels = english ? en : zh
  const Page = SECTION_PAGES[section]

  const select = (next: SettingsSection): void => {
    triggerHaptic('open')
    setSettingsSection(next)
  }

  return (
    <div className={styles.layout}>
      <nav aria-label={english ? 'Settings categories' : '设置分类'} className={styles.directory}>
        {GROUPS.map(group => (
          <div className={styles.group} key={group.zh}>
            <h2>{english ? group.en : group.zh}</h2>
            {group.sections.map(id => {
              const Icon = SECTION_ICONS[id]

              return (
                <button
                  aria-current={section === id ? 'page' : undefined}
                  className={styles.category}
                  key={id}
                  onClick={() => select(id)}
                  type="button"
                >
                  <Icon aria-hidden="true" size={16} />
                  <span>{labels[id]}</span>
                </button>
              )
            })}
          </div>
        ))}
      </nav>
      <div className={styles.contentColumn}>
        <div className={styles.mobileDirectory}>
          <span>{english ? 'Settings' : '设置'}</span>
          <PanelSelect
            ariaLabel={english ? 'Settings category' : '当前设置分类'}
            onChange={select}
            options={SETTINGS_SECTIONS.map(id => ({ value: id, label: labels[id] }))}
            value={section}
            widthClass="w-full"
          />
        </div>
        <div className={styles.scroller} key={section}>
          <div className={styles.content} data-section={section}>
            <Page />
          </div>
        </div>
      </div>
    </div>
  )
}
