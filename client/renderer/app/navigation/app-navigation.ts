import { atom, computed } from 'nanostores'

import { $companionSessionId, ensureCompanionSession, setChatSession, switchSession } from '@/features/conversation'
import { log } from '@/shared/lib/log'
import { definePersistedAtom, registerCompanionStorageKey, registerStorageClearHandler } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'
import { getStrings } from '@/shared/strings'
import {
  type AppLocation,
  type AppNavigationTarget,
  type AppPage,
  COMPANION_SECTIONS,
  type CompanionSection,
  composeNavigation,
  CREATION_SECTIONS,
  type CreationSection,
  DEFAULT_APP_LOCATION,
  DEFAULT_NAVIGATION,
  DEFAULT_SECTION,
  LIFE_SECTIONS,
  type LifeSection,
  navigationHash,
  normalizeAppLocation,
  normalizeAppNavigation,
  type PageFocus,
  parseNavigationHash,
  SETTINGS_SECTIONS,
  SETTINGS_TABS,
  type SettingsSection,
  type SettingsTab
} from '@ipc/contracts'

export { SETTINGS_SECTIONS }

const NAVIGATION_KEY = registerCompanionStorageKey('da.app.navigation')

/** 持久化只落位置：分区与页签随账户保存，切换模式时交接；整体替换，读取时规范化。 */
const location = definePersistedAtom<AppLocation>({
  key: NAVIGATION_KEY,
  fallback: DEFAULT_APP_LOCATION,
  normalize: normalizeAppLocation
})

/** 定位细节只在本窗口有效，不落盘。 */
const pageFocus = atom<PageFocus>({ page: 'chat' })

export const $appLocation = location.$atom
export const $appPage = computed($appLocation, value => value.page)
export const $lifeSection = computed($appLocation, value =>
  value.page === 'life' ? value.section : DEFAULT_SECTION.life
)
export const $creationSection = computed($appLocation, value =>
  value.page === 'creation' ? value.section : DEFAULT_SECTION.creation
)
export const $companionSection = computed($appLocation, value =>
  value.page === 'companion' ? value.section : DEFAULT_SECTION.companion
)
export const $settingsSection = computed($appLocation, value =>
  value.page === 'settings' ? value.section : DEFAULT_SECTION.settings
)
export const $settingsTab = computed($appLocation, value => (value.page === 'settings' ? value.tab : undefined))
export const $appNavigationTarget = computed([$appLocation, pageFocus], (current, focus) =>
  composeNavigation(current, focus)
)
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
  return parseNavigationHash(window.location.hash)
}

function writeHash(target: AppNavigationTarget): void {
  const next = `#${navigationHash(target)}`

  if (window.location.hash !== next) {
    replaceHash(next)
  }
}

export function getAppNavigation(): AppNavigationTarget {
  return composeNavigation(location.$atom.get(), pageFocus.get())
}

async function apply(target: AppNavigationTarget, updateHash = true): Promise<void> {
  const next = normalizeAppNavigation(target)
  location.replace(next.location)
  pageFocus.set(next.focus)
  $appNavigationRevision.set($appNavigationRevision.get() + 1)

  if (updateHash) {
    writeHash(next)
  }

  const sessionId = next.focus.page === 'chat' ? next.focus.sessionId : undefined

  if (sessionId) {
    setChatSession(sessionId)

    if ($gatewayState.get() === 'open') {
      await switchSession(sessionId)
    }
  }
}

export function applyAppNavigation(target: AppNavigationTarget, updateHash = true): Promise<void> {
  return apply(target, updateHash)
}

export function openChat(focus: Omit<Extract<PageFocus, { page: 'chat' }>, 'page'> = {}): Promise<void> {
  return apply(composeNavigation({ page: 'chat' }, { page: 'chat', ...focus }))
}

/** 进入伙伴主会话：已知会话时直接定位，未加载时才拉取；打不开时抛出可提示的错误。 */
export async function openCompanionMainChat(): Promise<string> {
  const sessionId = $companionSessionId.get() ?? (await ensureCompanionSession())

  if (!sessionId) {
    throw new Error(getStrings().chat.openMainSessionFailed)
  }

  await openChat({ sessionId })

  return sessionId
}

export function openLife(
  section: LifeSection = DEFAULT_SECTION.life,
  focus: Omit<Extract<PageFocus, { page: 'life' }>, 'page'> = {}
): Promise<void> {
  return apply(composeNavigation({ page: 'life', section }, { page: 'life', ...focus }))
}

export function openCreation(
  section: CreationSection = DEFAULT_SECTION.creation,
  focus: Omit<Extract<PageFocus, { page: 'creation' }>, 'page'> = {}
): Promise<void> {
  return apply(composeNavigation({ page: 'creation', section }, { page: 'creation', ...focus }))
}

export function openCompanion(section: CompanionSection = DEFAULT_SECTION.companion): Promise<void> {
  return apply(composeNavigation({ page: 'companion', section }, { page: 'companion' }))
}

export function openSettings(section: SettingsSection = DEFAULT_SECTION.settings, tab?: SettingsTab): Promise<void> {
  const next = tab ? { page: 'settings' as const, section, tab } : { page: 'settings' as const, section }

  return apply(composeNavigation(next, { page: 'settings' }))
}

export function setAppPage(page: AppPage): void {
  if (page === 'chat') {
    void openChat()
  } else if (page === 'life') {
    void openLife()
  } else if (page === 'creation') {
    void openCreation()
  } else if (page === 'companion') {
    void openCompanion()
  } else {
    void openSettings()
  }
}

registerStorageClearHandler(() => {
  pageFocus.set({ page: 'chat' })
  $appNavigationRevision.set(0)
  replaceHash(`#${navigationHash(DEFAULT_NAVIGATION)}`)
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
      void apply(target, false).catch(error => log.warn('app-navigation', 'navigation failed', error))
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

export { COMPANION_SECTIONS, CREATION_SECTIONS, DEFAULT_SECTION, LIFE_SECTIONS, SETTINGS_TABS }
export type { AppLocation, AppPage, CompanionSection, CreationSection, LifeSection, PageFocus, SettingsSection }
