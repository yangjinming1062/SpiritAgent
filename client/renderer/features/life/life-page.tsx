import { useStore } from '@nanostores/react'
import type React from 'react'

import { $lifeSection, openLife } from '@/app/navigation/app-navigation'
import { useStrings } from '@/shared/strings'
import type { AppPageProps } from '@/shared/types/app-page'
import { CapsuleTabs } from '@/ui'
import { SectionShell } from '@/ui/section-shell/section-shell'
import type { LifeSection } from '@ipc/contracts'

import { DiaryPage } from './diary/diary-page'
import { LifeOverviewPage } from './overview/life-overview-page'
import { PostsPage } from './posts/posts-page'

/** 生活页：近况、动态与日记共用一级入口，各自保留滚动与阅读状态。 */
export function LifePage({ containerRef, foreground, mediaViewId, visible }: AppPageProps): React.JSX.Element {
  const section = useStore($lifeSection)
  const strings = useStrings()
  const t = strings.life
  const rail = strings.living.rail

  const tabs = (
    <CapsuleTabs<LifeSection>
      ariaLabel={t.title}
      onChange={next => void openLife(next)}
      options={[
        { value: 'now', label: t.now },
        { value: 'posts', label: rail.posts },
        { value: 'diary', label: rail.diary }
      ]}
      value={section}
    />
  )

  return (
    <SectionShell activeId={section} ariaLabel={t.title} bounded tabs={tabs}>
      {section === 'now' ? (
        <LifeOverviewPage visible={visible} />
      ) : section === 'posts' ? (
        <PostsPage containerRef={containerRef} foreground={foreground} mediaViewId={mediaViewId} />
      ) : (
        <DiaryPage foreground={foreground} />
      )}
    </SectionShell>
  )
}
