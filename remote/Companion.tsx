import { IconDeviceTv } from '@tabler/icons-react'
import type { ReactElement } from 'react'

import { Actions } from './Actions'
import type { RemoteApi } from './api'
import { Assets } from './Assets'
import { MemoryView } from './Memory'
import { Profile } from './Profile'
import type { PlaybackReceipt } from './types'
import { focusAfterRender, PageHeader, usePageState } from './ui'
import { Visuals } from './Visuals'
import { Voice } from './Voice'

type Section = 'action' | 'memory' | 'outfit' | 'profile' | 'scene' | 'voice'

export function Companion({
  api,
  receipts,
  refresh
}: {
  api: RemoteApi
  receipts: PlaybackReceipt[]
  refresh: number
}): ReactElement {
  const [savedSection, setSection] = usePageState<Section>('companion.section', 'profile')
  const [visuals, setVisuals] = usePageState('companion.visuals', false)
  const items = [
    { id: 'profile', title: '资料' },
    { id: 'voice', title: '声音' },
    { id: 'memory', title: '记忆' },
    { id: 'outfit', title: '衣柜' },
    { id: 'scene', title: '环境' },
    { id: 'action', title: '动作' }
  ] as const
  const section = items.some(item => item.id === savedSection) ? savedSection : 'profile'

  if (visuals) {
    return (
      <div className="page companion-page">
        <Visuals
          api={api}
          onBack={() => {
            setVisuals(false)
            focusAfterRender('[data-visuals-entry]')
          }}
          receipts={receipts}
          refresh={refresh}
        />
      </div>
    )
  }

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
      <button className="visuals-entry" data-visuals-entry onClick={() => setVisuals(true)} type="button">
        <IconDeviceTv aria-hidden size={20} stroke={1.7} />
        <span>
          <strong>影像</strong>
          <small>浏览成品画面，重做或请求在电脑上播放</small>
        </span>
      </button>
      {section === 'profile' ? (
        <>
          <PageHeader subtitle="名字、性格和你们的相处方式" title="伙伴资料" />
          <Profile api={api} refresh={refresh} />
        </>
      ) : section === 'voice' ? (
        <Voice api={api} refresh={refresh} />
      ) : section === 'memory' ? (
        <>
          <PageHeader subtitle="查看、修改或忘记聊天中记住的事" title="记忆" />
          <MemoryView api={api} refresh={refresh} />
        </>
      ) : section === 'action' ? (
        <Actions api={api} refresh={refresh} />
      ) : (
        <Assets api={api} key={section} kind={section} refresh={refresh} />
      )}
    </div>
  )
}
