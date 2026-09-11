import { getUiEffect, getUiPalette, normalizeUiTheme, type SpiritAgentUiTheme } from '@ipc/contracts'
import { atom } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'

const THEME_STORAGE_KEY = 'da.ui.theme'

// 新装（localStorage 无此 key）默认走「日色透明」；已有显式选择（night/day/历史别名）保留。
const stored = storedString(THEME_STORAGE_KEY)
const initialTheme: SpiritAgentUiTheme = stored === null ? 'day-clear' : normalizeUiTheme(stored)

export const $theme = atom<SpiritAgentUiTheme>(initialTheme)

// 模块加载即应用：两个 entry 都先 import styles.css 再 import 本模块，
// 首帧渲染前 data-theme 已就位，无错误主题闪烁。
apply($theme.get())

function apply(theme: SpiritAgentUiTheme): void {
  const root = document.documentElement
  root.dataset.theme = theme
  root.dataset.palette = getUiPalette(theme)
  root.dataset.effect = getUiEffect(theme)
  $theme.set(theme)
  persistString(THEME_STORAGE_KEY, theme)
}

export function setUiTheme(theme: SpiritAgentUiTheme): void {
  apply(theme)
  // 主进程广播到两个窗口（含本窗，回声幂等）；持久化只由发起切换的窗口写。
  window.spiritagent?.setUiTheme?.(theme)
}

// 订阅主进程主题广播（另一窗口切换时同步本窗口）；两个 entry 的模块作用域各调用一次。
export function initUiThemeSync(): () => void {
  const unsubscribe = window.spiritagent?.onUiThemeChanged?.(payload => apply(normalizeUiTheme(payload?.theme)))

  return unsubscribe ?? (() => {})
}
