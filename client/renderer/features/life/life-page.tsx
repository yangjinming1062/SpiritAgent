import { useStore } from '@nanostores/react'
import type React from 'react'

import { $lifeSection, openLife } from '@/app/navigation/app-navigation'
import { BookOpen, Clock, Sparkles } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'
import type { AppPageProps } from '@/shared/types/app-page'
import { SectionShell } from '@/ui/section-shell/section-shell'
import type { LifeSection } from '@ipc/contracts'

import { $diaryHasUnread } from './diary'
import { DiaryPage } from './diary/diary-page'
import { LifeOverviewPage } from './overview/life-overview-page'
import { $postsHasUnread } from './posts'
import { PostsPage } from './posts/posts-page'

const ENTRIES = [
  { id: 'now', icon: Clock },
  { id: 'posts', icon: Sparkles },
  { id: 'diary', icon: BookOpen }
] as const

/** 生活页：近况、动态与日记共用一级入口，各自保留滚动与阅读状态。 */
export function LifePage({ containerRef, foreground, mediaViewId, visible }: AppPageProps): React.JSX.Element {
  const section = useStore($lifeSection)
  const postsUnread = useStore($postsHasUnread)
  const diaryUnread = useStore($diaryHasUnread)
  const strings = useStrings()
  const t = strings.life

  const content = (value: LifeSection): React.ReactNode => {
    switch (value) {
      case 'now':
        return <LifeOverviewPage visible={visible} />

      case 'posts':
        return <PostsPage containerRef={containerRef} foreground={foreground} mediaViewId={mediaViewId} />

      case 'diary':
        return <DiaryPage foreground={foreground} />
    }
  }

  return (
    <SectionShell
      activeId={section}
      ariaLabel={t.title}
      bounded
      entries={ENTRIES.map(({ id, icon }) => ({
        id,
        icon,
        label: id === 'now' ? t.now : strings.living.rail[id],
        hint: t.sectionHints[id],
        unread: id === 'posts' ? postsUnread : id === 'diary' ? diaryUnread : false
      }))}
      onSelect={next => void openLife(next)}
    >
      {content(section)}
    </SectionShell>
  )
}
