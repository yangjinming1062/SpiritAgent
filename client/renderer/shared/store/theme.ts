import { atom } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'
import { preferencesApi } from '@/shared/platform/desktop'
import {
  getUiEffect,
  getUiPalette,
  normalizeUiTheme,
  type SpiritAgentUiTheme,
  UI_THEME_URL_PARAM
} from '@ipc/contracts'

const THEME_STORAGE_KEY = 'da.ui.theme'

// 启动主题优先级：主进程播种的 URL 参数（窗口创建前写入）> localStorage（本窗即时缓存）>「日色透明」默认。URL 参数在模块加载时消费并清除（只在首帧前有效），之后主题变化一律走 IPC 广播（initUiThemeSync）与本窗 setUiTheme。
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

  return normalizeUiTheme(storedString(THEME_STORAGE_KEY))
}

const initialTheme = resolveInitialTheme()

export const $theme = atom<SpiritAgentUiTheme>(initialTheme)

// 各业务入口先加载样式再应用主题，首帧前 data-theme 已就位。
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
  // 主进程广播到各业务窗口（含本窗，回声幂等），各窗口写入本地缓存。
  preferencesApi().setUiTheme(theme)
}

// 各业务入口订阅主进程广播，同步窗口间的主题选择。
export function initUiThemeSync(): () => void {
  return preferencesApi().onUiThemeChanged(payload => apply(normalizeUiTheme(payload.theme)))
}
