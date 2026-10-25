import { atom, computed } from 'nanostores'

import { selectLifePursuit } from '@/app/features/life/life-state'
import { setChatSession, switchSession } from '@/modules/conversation'
import { log } from '@/shared/lib/log'
import { definePersistedAtom, registerCompanionStorageKey, registerStorageClearHandler } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'
import {
  APP_PAGES,
  type AppNavigationTarget,
  type AppPage,
  COMPANION_SECTIONS,
  type CompanionSection,
  normalizeAppNavigation,
  SETTINGS_SECTIONS,
  type SettingsSection
} from '@ipc/contracts'

export { APP_PAGES, SETTINGS_SECTIONS }

const NAVIGATION_KEY = registerCompanionStorageKey('da.app.navigation.v2')

const navigation = definePersistedAtom<AppNavigationTarget>({
  key: NAVIGATION_KEY,
  fallback: { page: 'chat', section: 'companionship', companionSection: 'profile' },
  isPersistable: (value): value is AppNavigationTarget => {
    if (!value || typeof value !== 'object') {
      return false
    }

    const candidate = value as Record<string, unknown>

    return (
      APP_PAGES.some(page => page === candidate.page) &&
      SETTINGS_SECTIONS.some(section => section === candidate.section) &&
      COMPANION_SECTIONS.some(section => section === candidate.companionSection) &&
      (candidate.settingsTab === undefined ||
        candidate.settingsTab === 'skills' ||
        candidate.settingsTab === 'toolsets')
    )
  }
})

/** 瞬时导航字段：会话与消息定位不落盘。 */
const transientNavigation = atom<{ messageKey?: string; sessionId?: string }>({})

export const $appPage = computed(navigation.$atom, value => value.page ?? 'chat')
export const $settingsSection = computed(navigation.$atom, value => value.section ?? 'companionship')
export const $companionSection = computed(navigation.$atom, value => value.companionSection ?? 'profile')
// 导航只有一个真源：持久字段与瞬时字段合成同一视图，两侧读到的是同一个页面与分区。
export const $appNavigationTarget = computed([navigation.$atom, transientNavigation], (durable, transient) => ({
  ...durable,
  ...transient
}))
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
  const [path, query] = window.location.hash.replace(/^#\/?/, '').split('?')
  const [page, detail] = path.split('/')

  if (!APP_PAGES.some(candidate => candidate === page)) {
    return null
  }

  const params = new URLSearchParams(query)

  return normalizeAppNavigation({
    page,
    section: page === 'settings' ? detail : undefined,
    companionSection: page === 'companion' ? detail : undefined,
    settingsTab: params.get('subtab'),
    sessionId: params.get('sessionId'),
    messageKey: params.get('messageKey'),
    pursuitId: params.has('pursuitId') ? Number(params.get('pursuitId')) : undefined
  })
}

function writeHash(target: AppNavigationTarget): void {
  const query = new URLSearchParams()

  if (target.page === 'settings' && target.section === 'devices' && target.settingsTab) {
    query.set('subtab', target.settingsTab)
  }

  if (target.sessionId) {
    query.set('sessionId', target.sessionId)
  }

  if (target.messageKey) {
    query.set('messageKey', target.messageKey)
  }

  if (target.page === 'life' && target.pursuitId) {
    query.set('pursuitId', String(target.pursuitId))
  }

  const detail =
    target.page === 'settings' ? target.section : target.page === 'companion' ? target.companionSection : undefined

  const next = `#/${target.page}${detail ? `/${detail}` : ''}${query.size ? `?${query}` : ''}`

  if (window.location.hash !== next) {
    replaceHash(next)
  }
}

export function getAppNavigation(): AppNavigationTarget {
  return { ...navigation.$atom.get() }
}

export async function applyAppNavigation(target: AppNavigationTarget, updateHash = true): Promise<void> {
  const next = normalizeAppNavigation(target)
  const previous = navigation.$atom.get()
  navigation.set({
    ...previous,
    page: next.page ?? (next.sessionId ? 'chat' : previous.page),
    section: next.section ?? previous.section,
    companionSection: next.companionSection ?? previous.companionSection,
    settingsTab: next.section ? next.settingsTab : previous.settingsTab
  })

  if (next.sessionId) {
    setChatSession(next.sessionId)
  }

  if (next.page === 'life' && next.pursuitId) {
    selectLifePursuit(next.pursuitId)
  }

  transientNavigation.set({ messageKey: next.messageKey, sessionId: next.sessionId })
  $appNavigationRevision.set($appNavigationRevision.get() + 1)

  if (updateHash) {
    writeHash({
      ...getAppNavigation(),
      sessionId: next.sessionId,
      messageKey: next.messageKey,
      pursuitId: next.pursuitId
    })
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

export function setCompanionSection(companionSection: CompanionSection): void {
  void applyAppNavigation({ page: 'companion', companionSection })
}

registerStorageClearHandler(() => {
  transientNavigation.set({})
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
