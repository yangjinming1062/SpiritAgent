import type { ReactElement } from 'react'

import type { RemoteApi, RemoteGateway } from './api'
import { DiaryView, Posts } from './Feed'
import { LifeSummary } from './LifeSummary'
import { usePageState } from './page-state'
import { PageHeader, SectionNav } from './ui'

type Section = 'diary' | 'now' | 'posts'

const ITEMS = [
  { id: 'now', title: '近况', subtitle: '伙伴自己的日常、在意的事与真实成果' },
  { id: 'posts', title: '动态', subtitle: '伙伴分享的文字、图片与视频' },
  { id: 'diary', title: '日记', subtitle: '按日期留下的记录与感受' }
] as const

/** 生活页签：与桌面同一套三分区目录；分区随会话保存，未读点归各自分区。 */
export function Life({
  api,
  gateway,
  onRead,
  refresh,
  unread
}: {
  api: RemoteApi
  gateway: RemoteGateway
  onRead: () => void
  refresh: number
  unread: { feed: boolean; diary: boolean }
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
        unread={{ posts: unread.feed, diary: unread.diary }}
      />
      <PageHeader subtitle={active.subtitle} title={active.title} />
      {section === 'now' ? (
        <LifeSummary api={api} refresh={refresh} />
      ) : section === 'posts' ? (
        <Posts api={api} gateway={gateway} onRead={onRead} refresh={refresh} />
      ) : (
        <DiaryView api={api} onRead={onRead} refresh={refresh} />
      )}
    </div>
  )
}
