import type React from 'react'

import { CompanionHub } from '@/features/companion/companion-page'
import { CreationPage } from '@/features/creation/creation-page'
import { LifePage } from '@/features/life/life-page'
import { AppSettings } from '@/features/settings/app-settings'
import type { AppPageProps } from '@/shared/types/app-page'
import type { AppPage } from '@ipc/contracts'

// 页面与一级入口一一对应；新增页面在这里登记，不在一处 switch 里散落分支。
const PAGE_VIEWS = {
  life: LifePage,
  creation: CreationPage,
  companion: CompanionHub,
  settings: AppSettings
} satisfies Record<Exclude<AppPage, 'chat'>, React.ComponentType<AppPageProps>>

export function AppContentPage({
  page,
  ...props
}: AppPageProps & { page: Exclude<AppPage, 'chat'> }): React.JSX.Element {
  const View = PAGE_VIEWS[page]

  return <View {...props} />
}
