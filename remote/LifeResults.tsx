import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'

import { type LifeEpisode, lifeResultKey, lifeResults } from '../shared/life-trajectory'

import type { RemoteApi } from './api'
import { useAlive, useTask } from './tasks'
import { Media, Notice } from './ui'

type ResultLink = ReturnType<typeof lifeResults>[number]
const resultLabels = { visual: '查看影像', outfit: '查看造型', scene: '查看环境', action: '查看动作' }

function LifeResult({ api, link }: { api: RemoteApi; link: ResultLink }): ReactElement {
  const [preview, setPreview] = useState<ReactNode>(null)
  const [open, setOpen] = useState(false)
  const alive = useAlive()
  const { busy, error, run } = useTask()

  const load = async (): Promise<void> => {
    const { id, kind } = link
    let body: ReactNode

    switch (kind) {
      case 'visual': {
        const item = await api.invoke('companion_visuals.get_visual_version', {
          path: { visual_id: id, version_id: link.versionId }
        })
        body = (
          <>
            {item.title && <strong>{item.title}</strong>}
            <Media type={item.media_kind} url={item.media_url} />
          </>
        )
        break
      }
      case 'outfit': {
        const { outfits } = await api.invoke('companion.get_outfits')
        const item = outfits.find(outfit => outfit.id === id)
        body = item ? <Media type="image" url={item.fullbody_url} /> : <p>这件造型已无法查看。</p>
        break
      }
      case 'scene': {
        const item = await api.invoke('companion_scenes.scene_detail_route', { path: { scene_id: id } })
        body = <Media type="image" url={item.url} />
        break
      }
      case 'action': {
        const item = await api.invoke('companion_actions.get_action', { path: { action_id: id } })
        body = (
          <>
            <strong>{item.name}</strong>
            <p>{item.motion_description}</p>
          </>
        )
        break
      }
    }

    if (alive()) {
      setPreview(body)
    }
  }

  return (
    <details
      className="life-notes"
      onToggle={event => {
        const next = event.currentTarget.open
        setOpen(next)

        if (next && !preview && !busy) {
          void run(load)
        }
      }}
    >
      <summary>{resultLabels[link.kind]}</summary>
      {open && preview}
      {open && !preview && busy && <p role="status">正在加载…</p>}
      {open && error && (
        <Notice>
          {error}
          <button disabled={busy} onClick={() => void run(load)} type="button">
            重试
          </button>
        </Notice>
      )}
    </details>
  )
}

export function LifeResultPreviews({
  api,
  reference
}: {
  api: RemoteApi
  reference: LifeEpisode['result_ref']
}): ReactElement {
  return (
    <>
      {lifeResults(reference).map(link => (
        <LifeResult api={api} key={lifeResultKey(link)} link={link} />
      ))}
    </>
  )
}
