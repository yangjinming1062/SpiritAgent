import type React from 'react'

import { CompanionHub } from '@/app/features/companion/companion-page'
import { DiaryPage } from '@/app/features/diary/diary-page'
import { LifePage } from '@/app/features/life/life-page'
import { PostsPage } from '@/app/features/posts/posts-page'
import { AppSettings } from '@/app/features/settings/app-settings'
import type { AppPage } from '@ipc/contracts'

export function AppContentPage({
  page,
  containerRef,
  foreground,
  visible,
  mediaViewId,
  standaloneLife = false
}: {
  page: Exclude<AppPage, 'chat'>
  containerRef: React.RefObject<HTMLElement | null>
  foreground: boolean
  visible: boolean
  mediaViewId: string
  standaloneLife?: boolean
}): React.JSX.Element {
  switch (page) {
    case 'posts':
      return <PostsPage containerRef={containerRef} foreground={foreground} mediaViewId={mediaViewId} />

    case 'diary':
      return <DiaryPage foreground={foreground} />

    case 'life':
      return <LifePage standalone={standaloneLife} visible={visible} />

    case 'companion':
      return <CompanionHub />

    case 'settings':
      return <AppSettings />
  }
}
