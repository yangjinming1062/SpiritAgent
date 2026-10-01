// 视图通过 hash 与 localStorage 持久化，重开生活空间时恢复。

import { atom } from 'nanostores'

import { normalizeHashPath } from '@/shared/lib/hash-route'
import { definePersistedEnum } from '@/shared/lib/storage'

const LIVING_VIEWS = ['chat', 'appearance', 'moments', 'diary', 'channels', 'scene', 'settings'] as const

export type LivingView = (typeof LIVING_VIEWS)[number]

export const LIVING_SETTINGS_SECTIONS = ['persona', 'voice', 'interaction', 'theme', 'shortcuts', 'about'] as const

export type LivingSettingsSection = (typeof LIVING_SETTINGS_SECTIONS)[number]

function hashSegments(): string[] {
  return normalizeHashPath(window.location.hash).split('/')
}

function pick<T extends string>(allowed: readonly T[], value: string | undefined): T | null {
  return allowed.find(item => item === value) ?? null
}

function parseLivingViewFromHash(): LivingView | null {
  return pick(LIVING_VIEWS, hashSegments()[0])
}

function parseLivingSettingsSectionFromHash(): LivingSettingsSection | null {
  const segments = hashSegments()

  return segments[0] === 'settings' ? pick(LIVING_SETTINGS_SECTIONS, segments[1]) : null
}

function replaceHash(next: string): void {
  if (window.location.hash !== next) {
    window.history.replaceState(null, '', next)
  }
}

const viewStore = definePersistedEnum<LivingView>({
  allowed: LIVING_VIEWS,
  fallback: 'chat',
  key: 'da.living.view'
})

// settings 视图用 #/settings/<section> 形式深链。
const sectionStore = definePersistedEnum<LivingSettingsSection>({
  allowed: LIVING_SETTINGS_SECTIONS,
  fallback: 'persona',
  key: 'da.living.settings.section'
})

export const $livingView = atom<LivingView>(parseLivingViewFromHash() ?? viewStore.get())
export const $livingSettingsSection = atom<LivingSettingsSection>(
  parseLivingSettingsSectionFromHash() ?? sectionStore.get()
)

function commitView(view: LivingView): void {
  viewStore.set(view)
  $livingView.set(view)
}

function commitSection(section: LivingSettingsSection): void {
  sectionStore.set(section)
  $livingSettingsSection.set(section)
}

export function setLivingView(view: LivingView): void {
  commitView(view)

  if (view === 'settings') {
    replaceHash(`#/settings/${$livingSettingsSection.get()}`)
  } else {
    replaceHash(`#/${view}`)
  }
}

export function setLivingSettingsSection(section: LivingSettingsSection): void {
  commitView('settings')
  commitSection(section)
  replaceHash(`#/settings/${section}`)
}

function onHashChange(): void {
  const view = parseLivingViewFromHash()

  if (view && $livingView.get() !== view) {
    commitView(view)
  }

  const section = parseLivingSettingsSectionFromHash()

  if (section && $livingSettingsSection.get() !== section) {
    commitSection(section)
  }
}

window.addEventListener('hashchange', onHashChange)
