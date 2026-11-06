import { IconDeviceTv, IconHome, IconShirt, IconSparkles } from '@tabler/icons-react'
import type { ReactElement } from 'react'

import { Actions } from './Actions'
import type { RemoteApi } from './api'
import { Assets } from './Assets'
import { usePageState } from './ui'
import { Visuals } from './Visuals'

type Section = 'action' | 'outfit' | 'scene' | 'visuals'

const ITEMS = [
  { id: 'visuals', title: '影像', icon: IconDeviceTv },
  { id: 'outfit', title: '造型', icon: IconShirt },
  { id: 'scene', title: '环境', icon: IconHome },
  { id: 'action', title: '动作', icon: IconSparkles }
] as const

/** 创作页签：与桌面同一套入口顺序；分区随会话保存，切走再回来不丢创作位置。 */
export function Creation({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [section, setSection] = usePageState<Section>('creation.section', 'visuals')

  return (
    <div className="page creation-page">
      <nav aria-label="创作分类" className="companion-nav">
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
      {section === 'visuals' ? (
        <Visuals api={api} onOpenSection={setSection} refresh={refresh} />
      ) : section === 'action' ? (
        <Actions api={api} refresh={refresh} />
      ) : (
        <Assets api={api} key={section} kind={section} refresh={refresh} />
      )}
    </div>
  )
}
