import { initCompanionPrefsSync } from '@/modules/character'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { initLocaleSync } from '@/shared/store/locale'
import { hydratePresentation } from '@/shared/store/presentation'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

import { bindPresentation } from './bind-presentation'

// 各窗口入口在渲染前调用一次的公共初始化；窗口角色（setSurfaceRole）由入口先行登记。
export function initRenderer(): void {
  applyNoBlurIfNeeded()
  bindPresentation()
  initUiThemeSync()
  initLocaleSync()
  initCompanionPrefsSync()
  hydrateSurfaces()
  hydratePresentation()
}
