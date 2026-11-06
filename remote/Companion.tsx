import type { ReactElement } from 'react'

import type { RemoteApi } from './api'
import { MemoryView } from './Memory'
import { Profile } from './Profile'
import { PageHeader, usePageState } from './ui'
import { Voice } from './Voice'

type Section = 'memory' | 'profile' | 'voice'

const ITEMS = [
  { id: 'profile', title: '资料' },
  { id: 'voice', title: '声音' },
  { id: 'memory', title: '记忆' }
] as const

/** 伙伴页签只保留"它是谁"：资料、声音与记忆；创作相关入口在创作页。 */
export function Companion({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [savedSection, setSection] = usePageState<Section>('companion.section', 'profile')
  const section = ITEMS.some(item => item.id === savedSection) ? savedSection : 'profile'

  return (
    <div className="page companion-page">
      <nav aria-label="伙伴分类" className="companion-nav">
        {ITEMS.map(item => (
          <button
            aria-current={section === item.id ? 'page' : undefined}
            key={item.id}
            onClick={() => setSection(item.id)}
            type="button"
          >
            {item.title}
          </button>
        ))}
      </nav>
      {section === 'profile' ? (
        <>
          <PageHeader subtitle="名字、性格和你们的相处方式" title="伙伴资料" />
          <Profile api={api} refresh={refresh} />
        </>
      ) : section === 'voice' ? (
        <Voice api={api} refresh={refresh} />
      ) : (
        <>
          <PageHeader subtitle="查看、修改或忘记聊天中记住的事" title="记忆" />
          <MemoryView api={api} refresh={refresh} />
        </>
      )}
    </div>
  )
}
