import type React from 'react'

import { CompanionHub } from '@/app/features/companion/companion-page'
import { DiaryPage } from '@/app/features/diary/diary-page'
import { PostsPage } from '@/app/features/posts/posts-page'
import { AppSettings } from '@/app/features/settings/app-settings'
import { VisualsPage } from '@/app/features/visuals/visuals-page'
import type { AppPage } from '@ipc/contracts'

export function AppContentPage({
  page,
  containerRef,
  foreground,
  mediaViewId
}: {
  page: Exclude<AppPage, 'chat'>
  containerRef: React.RefObject<HTMLElement | null>
  foreground: boolean
  mediaViewId: string
}): React.JSX.Element {
  switch (page) {
    case 'posts':
      return <PostsPage containerRef={containerRef} foreground={foreground} mediaViewId={mediaViewId} />

    case 'diary':
      return <DiaryPage foreground={foreground} />

    case 'visuals':
      return <VisualsPage />

    case 'companion':
      return <CompanionHub />

    case 'settings':
      return <AppSettings />
  }
}
