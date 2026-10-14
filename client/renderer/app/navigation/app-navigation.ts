import { atom } from 'nanostores'

import { setChatSession, switchSession } from '@/modules/conversation'
import { log } from '@/shared/lib/log'
import { definePersistedEnum, registerStorageClearHandler } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'
import {
  APP_PAGES,
  type AppNavigationTarget,
  type AppPage,
  normalizeAppNavigation,
  SETTINGS_SECTIONS,
  type SettingsSection
} from '@ipc/contracts'

export { APP_PAGES, SETTINGS_SECTIONS }

const pageStore = definePersistedEnum<AppPage>({ allowed: APP_PAGES, fallback: 'chat', key: 'da.app.page' })

const sectionStore = definePersistedEnum<SettingsSection>({
  allowed: SETTINGS_SECTIONS,
  fallback: 'persona',
  key: 'da.app.settings.section'
})

export const $appPage = pageStore.$atom
export const $settingsSection = sectionStore.$atom
export const $appNavigationTarget = atom<AppNavigationTarget>({})
export const $appNavigationRevision = atom(0)
let writingHash = false

function replaceHash(next: string): void {
  writingHash = true

  try {
    window.history.replaceState(window.history.state, '', next)
    window.dispatchEvent(new PopStateEvent('popstate'))
  } finally {
    writingHash = false
  }
}

export function readAppNavigationHash(): AppNavigationTarget | null {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [path, query] = raw.split('?')
  const [page, section] = path.split('/')

  if (!APP_PAGES.some(candidate => candidate === page)) {
    return null
  }

  const params = new URLSearchParams(query)

  return normalizeAppNavigation({
    page,
    section: page === 'settings' ? section : undefined,
    settingsTab: params.get('subtab') ?? undefined,
    sessionId: params.get('sessionId') ?? undefined,
    messageKey: params.get('messageKey') ?? undefined
  })
}

function writeHash(target: AppNavigationTarget): void {
  const page = target.page ?? $appPage.get()
  const section = target.section ?? $settingsSection.get()
  const query = new URLSearchParams()

  if (page === 'settings' && section === 'skills') {
    const tab = target.settingsTab ?? getAppNavigation().settingsTab

    if (tab) {
      query.set('subtab', tab)
    }
  }

  if (target.sessionId) {
    query.set('sessionId', target.sessionId)
  }

  if (target.messageKey) {
    query.set('messageKey', target.messageKey)
  }

  const suffix = query.size ? `?${query}` : ''
  const next = `#/${page}${page === 'settings' ? `/${section}` : ''}${suffix}`

  if (window.location.hash !== next) {
    replaceHash(next)
  }
}

export function getAppNavigation(): AppNavigationTarget {
  return {
    page: $appPage.get(),
    section: $appPage.get() === 'settings' ? $settingsSection.get() : undefined,
    settingsTab:
      $appPage.get() === 'settings' && $settingsSection.get() === 'skills'
        ? readAppNavigationHash()?.settingsTab
        : undefined
  }
}

export async function applyAppNavigation(target: AppNavigationTarget, updateHash = true): Promise<void> {
  const next = normalizeAppNavigation(target)

  if (next.section) {
    sectionStore.set(next.section)
  }

  pageStore.set(next.page ?? (next.section ? 'settings' : next.sessionId ? 'chat' : $appPage.get()))

  if (next.sessionId) {
    setChatSession(next.sessionId)
  }

  $appNavigationTarget.set(next)
  $appNavigationRevision.set($appNavigationRevision.get() + 1)

  if (updateHash) {
    writeHash({ ...next, page: $appPage.get() })
  }

  if (next.sessionId && $gatewayState.get() === 'open') {
    await switchSession(next.sessionId)
  }
}

export function setAppPage(page: AppPage): void {
  void applyAppNavigation({ page })
}

export function setSettingsSection(section: SettingsSection): void {
  void applyAppNavigation({ page: 'settings', section })
}

registerStorageClearHandler(() => {
  $appNavigationTarget.set({})
  $appNavigationRevision.set(0)
  replaceHash('#/chat')
})

let stopNavigation: (() => void) | undefined

export function initAppNavigation(): () => void {
  if (stopNavigation) {
    return stopNavigation
  }

  const onHashChange = (): void => {
    if (writingHash) {
      return
    }

    const target = readAppNavigationHash()

    if (target) {
      void applyAppNavigation(target, false).catch(error => log.warn('app-navigation', 'navigation failed', error))
    }
  }

  onHashChange()
  window.addEventListener('hashchange', onHashChange)
  window.addEventListener('popstate', onHashChange)

  stopNavigation = () => {
    window.removeEventListener('hashchange', onHashChange)
    window.removeEventListener('popstate', onHashChange)
    stopNavigation = undefined
  }

  return stopNavigation
}
