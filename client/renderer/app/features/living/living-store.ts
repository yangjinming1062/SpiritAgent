// 视图通过 hash 与 localStorage 持久化，重开生活空间时恢复。

import { normalizeHashPath } from '@/shared/lib/hash-route'
import { definePersistedEnum } from '@/shared/lib/storage'

const LIVING_VIEWS = ['chat', 'appearance', 'posts', 'diary', 'remote', 'scene', 'settings'] as const

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

// 直接导出持久化 store 的 atom，账户切换的 clear/restore 与展示同源。
export const $livingView = viewStore.$atom
export const $livingSettingsSection = sectionStore.$atom

// 启动时 hash 深链优先于持久值展示，但只覆盖 atom 不落盘。
const hashView = parseLivingViewFromHash()

if (hashView) {
  $livingView.set(hashView)
}

const hashSection = parseLivingSettingsSectionFromHash()

if (hashSection) {
  $livingSettingsSection.set(hashSection)
}

export function setLivingView(view: LivingView): void {
  viewStore.set(view)

  if (view === 'settings') {
    replaceHash(`#/settings/${$livingSettingsSection.get()}`)
  } else {
    replaceHash(`#/${view}`)
  }
}

export function setLivingSettingsSection(section: LivingSettingsSection): void {
  viewStore.set('settings')
  sectionStore.set(section)
  replaceHash(`#/settings/${section}`)
}

// 只同步状态不回写 hash，保留用户深链原样。
function onHashChange(): void {
  const view = parseLivingViewFromHash()

  if (view && $livingView.get() !== view) {
    viewStore.set(view)
  }

  const section = parseLivingSettingsSectionFromHash()

  if (section && $livingSettingsSection.get() !== section) {
    sectionStore.set(section)
  }
}

window.addEventListener('hashchange', onHashChange)
