import type { ReactElement } from 'react'

import { Actions } from './Actions'
import type { RemoteApi } from './api'
import { usePageState } from './page-state'
import { SceneLibrary } from './SceneLibrary'
import { SectionNav } from './ui'
import { Visuals } from './Visuals'
import { Wardrobe } from './Wardrobe'

const ITEMS = [
  { id: 'visuals', title: '影像' },
  { id: 'outfit', title: '造型' },
  { id: 'scene', title: '环境' },
  { id: 'action', title: '动作' }
] as const

export type CreationSection = (typeof ITEMS)[number]['id']

export function useCreationSection() {
  const [savedSection, setSection] = usePageState<CreationSection>('creation.section', 'visuals')
  const section = ITEMS.find(item => item.id === savedSection)?.id ?? ITEMS[0].id
  return [section, setSection] as const
}

export function Creation({
  api,
  refresh,
  section,
  onSelect
}: {
  api: RemoteApi
  refresh: number
  section: CreationSection
  onSelect: (section: CreationSection) => void
}): ReactElement {
  return (
    <div className="page creation-page">
      <SectionNav items={ITEMS} label="创作分类" onSelect={onSelect} selected={section} />
      {section === 'visuals' ? (
        <Visuals api={api} onOpenSection={onSelect} refresh={refresh} />
      ) : section === 'action' ? (
        <Actions api={api} refresh={refresh} />
      ) : section === 'scene' ? (
        <SceneLibrary api={api} refresh={refresh} />
      ) : (
        <Wardrobe api={api} refresh={refresh} />
      )}
    </div>
  )
}
