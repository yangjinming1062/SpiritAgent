import {
  getUiEffect,
  getUiPalette,
  normalizeUiTheme,
  type SpiritAgentUiTheme,
  UI_THEME_URL_PARAM
} from '@ipc/contracts'
import { atom } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'

const THEME_STORAGE_KEY = 'da.ui.theme'

// 启动主题的优先级：主进程播种的 URL 参数（配置镜像当前值，窗口创建前写入）>
// localStorage（本窗口上次使用的即时缓存）> 「日色透明」默认。
// URL 参数在模块加载时消费并清除——它只在首帧前有效，之后主题变化一律走
// IPC 广播（initUiThemeSync）与本窗 setUiTheme。
function resolveInitialTheme(): SpiritAgentUiTheme {
  const params = new URLSearchParams(window.location.search)
  const seeded = params.get(UI_THEME_URL_PARAM)

  if (seeded !== null) {
    params.delete(UI_THEME_URL_PARAM)
    const query = params.toString()
    const path = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`

    try {
      window.history.replaceState(window.history.state, '', path)
    } catch {
      // 受限 History 环境只影响参数清理，不影响镜像主题的优先级。
    }

    return normalizeUiTheme(seeded)
  }

  const stored = storedString(THEME_STORAGE_KEY)

  return stored === null ? 'day-clear' : normalizeUiTheme(stored)
}

const initialTheme = resolveInitialTheme()

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
