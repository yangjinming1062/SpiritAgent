import { useStore } from '@nanostores/react'
import type React from 'react'

import { $lifeSection, openLife } from '@/app/navigation/app-navigation'
import { BookOpen, Clock } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'
import type { AppPageProps } from '@/shared/types/app-page'
import { SectionShell } from '@/ui/section-shell/section-shell'
import type { LifeSection } from '@ipc/contracts'

import { $diaryHasUnread } from './diary'
import { DiaryPage } from './diary/diary-page'
import { LifeOverviewPage } from './overview/life-overview-page'

const ENTRIES = [
  { id: 'now', icon: Clock },
  { id: 'diary', icon: BookOpen }
] as const

export function LifePage({ foreground, visible }: AppPageProps): React.JSX.Element {
  const section = useStore($lifeSection)
  const diaryUnread = useStore($diaryHasUnread)
  const strings = useStrings()
  const t = strings.life

  const content = (value: LifeSection): React.ReactNode => {
    switch (value) {
      case 'now':
        return <LifeOverviewPage visible={visible} />

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
        unread: id === 'diary' && diaryUnread
      }))}
      onSelect={next => void openLife(next)}
    >
      {content(section)}
    </SectionShell>
  )
}
