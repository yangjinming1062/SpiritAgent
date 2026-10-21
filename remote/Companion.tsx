import type { ReactElement } from 'react'

import type { RemoteApi } from './api'
import { Assets } from './Assets'
import { MemoryView } from './Memory'
import { Profile } from './Profile'
import { PageHeader, usePageState } from './ui'

type Section = 'profile' | 'memory' | 'scene' | 'outfit'

export function Companion({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [savedSection, setSection] = usePageState<Section>('companion.section', 'profile')
  const items = [
    { id: 'profile', title: '资料' },
    { id: 'memory', title: '记忆' },
    { id: 'outfit', title: '衣柜' },
    { id: 'scene', title: '窗口背景' }
  ] as const
  const section = items.some(item => item.id === savedSection) ? savedSection : 'profile'

  return (
    <div className="page companion-page">
      <nav aria-label="伙伴分类" className="companion-nav">
        {items.map(item => (
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
      ) : section === 'memory' ? (
        <>
          <PageHeader subtitle="查看、修改或忘记聊天中记住的事" title="记忆" />
          <MemoryView api={api} refresh={refresh} />
        </>
      ) : (
        <Assets api={api} key={section} kind={section} refresh={refresh} />
      )}
    </div>
  )
}
