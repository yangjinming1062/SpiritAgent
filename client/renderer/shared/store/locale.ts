import { atom } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'

import { type Locale, normalizeLocale } from '../strings/locales'

const LOCALE_STORAGE_KEY = 'da.locale'

// 模块加载即按 localStorage 还原，避免首帧渲染前字典未就位造成闪屏。
const initialLocale: Locale = normalizeLocale(storedString(LOCALE_STORAGE_KEY))

export const $locale = atom<Locale>(initialLocale)

export function setLocale(locale: Locale): void {
  $locale.set(locale)
  persistString(LOCALE_STORAGE_KEY, locale)
  // 经 prefs:set 通道写——渲染层只能走此通道，否则被白名单外绕过。
  window.spiritagent?.prefs?.set({ key: 'language', value: locale })
}

export function initLocaleSync(): () => void {
  const unsubscribe = window.spiritagent?.onPrefsHydrated?.(payload => {
    const next = normalizeLocale(payload?.language)

    if (next !== $locale.get()) {
      $locale.set(next)
      persistString(LOCALE_STORAGE_KEY, next)
    }
  })

  return unsubscribe ?? (() => {})
}
