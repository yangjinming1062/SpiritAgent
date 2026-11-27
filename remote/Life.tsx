import type { ReactElement } from 'react'

import type { RemoteApi } from './api'
import { DiaryView } from './Diary'
import { LifeSummary } from './LifeSummary'
import { usePageState } from './page-state'
import { PageHeader, SectionNav } from './ui'

const ITEMS = [
  { id: 'now', title: '近况' },
  { id: 'diary', title: '日记', subtitle: '按日期留下的记录与感受' }
] as const

type Section = (typeof ITEMS)[number]['id']

export function Life({
  api,
  onRead,
  refresh,
  diaryUnread
}: {
  api: RemoteApi
  onRead: () => void
  refresh: number
  diaryUnread: boolean
}): ReactElement {
  const [savedSection, setSection] = usePageState<Section>('life.section', 'now')
  const active = ITEMS.find(item => item.id === savedSection) ?? ITEMS[0]
  const section = active.id

  return (
    <div className="page life-page">
      <SectionNav
        items={ITEMS}
        label="生活分类"
        onSelect={setSection}
        selected={section}
        unread={{ diary: diaryUnread }}
      />
      {active.id !== 'now' && <PageHeader subtitle={active.subtitle} title={active.title} />}
      {section === 'now' ? (
        <LifeSummary api={api} refresh={refresh} />
      ) : (
        <DiaryView api={api} onRead={onRead} refresh={refresh} />
      )}
    </div>
  )
}
