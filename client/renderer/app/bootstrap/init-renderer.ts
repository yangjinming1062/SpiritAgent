import { initAppNavigation } from '@/app/navigation/app-navigation'
import { installAppUiStateSync } from '@/app/workflows/app-ui-state'
import { initCompanionPrefsSync } from '@/modules/character'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { initLocaleSync } from '@/shared/store/locale'
import { hydratePresentation } from '@/shared/store/presentation'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

import { bindPresentation } from './bind-presentation'

// 各窗口入口在渲染前调用一次的公共初始化；窗口角色（setSurfaceRole）由入口先行登记。
export function initRenderer(): void {
  const stopNavigation = initAppNavigation()

  if (import.meta.hot) {
    import.meta.hot.dispose(stopNavigation)
  }

  applyNoBlurIfNeeded()
  bindPresentation()
  initUiThemeSync()
  initLocaleSync()
  initCompanionPrefsSync()
  hydrateSurfaces()
  hydratePresentation()
  const stopUiState = installAppUiStateSync()

  if (import.meta.hot) {
    import.meta.hot.dispose(stopUiState)
  }
}
