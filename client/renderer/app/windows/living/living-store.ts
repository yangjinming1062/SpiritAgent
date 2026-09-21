// 视图通过 hash 与 localStorage 持久化，重开生活空间时恢复。

import { atom } from 'nanostores'

import { normalizeHashPath } from '@/shared/lib/hash-route'
import { definePersistedEnum } from '@/shared/lib/storage'

export type LivingView = 'chat' | 'appearance' | 'moments' | 'diary' | 'channels' | 'scene' | 'settings'

export type LivingSettingsSection = 'persona' | 'voice' | 'interaction' | 'theme' | 'shortcuts' | 'about'

export const LIVING_VIEWS: ReadonlyArray<LivingView> = [
  'chat',
  'appearance',
  'moments',
  'diary',
  'channels',
  'scene',
  'settings'
]

export const LIVING_SETTINGS_SECTIONS: ReadonlyArray<LivingSettingsSection> = [
  'persona',
  'voice',
  'interaction',
  'theme',
  'shortcuts',
  'about'
]

const SETTINGS_SECTION_PREFIX = 'settings/'

function parseLivingViewFromHash(): LivingView | null {
  if (typeof window === 'undefined' || !window.location.hash) {
    return null
  }

  const topSegment = normalizeHashPath(window.location.hash).split('/')[0]

  return (LIVING_VIEWS as ReadonlyArray<string>).includes(topSegment) ? (topSegment as LivingView) : null
}

function parseLivingSettingsSectionFromHash(): LivingSettingsSection | null {
  if (typeof window === 'undefined' || !window.location.hash) {
    return null
  }

  const cleanPath = normalizeHashPath(window.location.hash)

  if (!cleanPath.startsWith(SETTINGS_SECTION_PREFIX)) {
    return null
  }

  const section = cleanPath.slice(SETTINGS_SECTION_PREFIX.length).split('/')[0]

  return (LIVING_SETTINGS_SECTIONS as ReadonlyArray<string>).includes(section)
    ? (section as LivingSettingsSection)
    : null
}

function replaceHash(next: string): void {
  if (typeof window !== 'undefined' && window.location.hash !== next) {
    window.history.replaceState(null, '', next)
  }
}

const viewStore = definePersistedEnum<LivingView>({
  allowed: LIVING_VIEWS,
  fallback: 'chat',
  key: 'da.living.view'
})

// settings 视图用 #/settings/<section> 形式深链；hash 解析时去掉 settings/ 前缀。
const sectionStore = definePersistedEnum<LivingSettingsSection>({
  allowed: LIVING_SETTINGS_SECTIONS,
  fallback: 'persona',
  key: 'da.living.settings.section'
})

export const $livingView = atom<LivingView>(parseLivingViewFromHash() ?? viewStore.get())
export const $livingSettingsSection = atom<LivingSettingsSection>(
  parseLivingSettingsSectionFromHash() ?? sectionStore.get()
)

export function setLivingView(view: LivingView): void {
  viewStore.set(view)
  $livingView.set(view)

  if (view === 'settings') {
    replaceHash(`#/settings/${$livingSettingsSection.get()}`)
  } else {
    replaceHash(`#/${view}`)
  }
}

export function setLivingSettingsSection(section: LivingSettingsSection): void {
  viewStore.set('settings')
  $livingView.set('settings')
  sectionStore.set(section)
  $livingSettingsSection.set(section)
  replaceHash(`#/settings/${section}`)
}

function onHashChange(): void {
  const view = parseLivingViewFromHash()

  if (view && $livingView.get() !== view) {
    viewStore.set(view)
    $livingView.set(view)
  }

  const section = parseLivingSettingsSectionFromHash()

  if (section && $livingSettingsSection.get() !== section) {
    sectionStore.set(section)
    $livingSettingsSection.set(section)
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', onHashChange)
}
