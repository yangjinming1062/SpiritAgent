import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'

import {
  formatLifeDay,
  groupLifeEpisodes,
  type LifeEpisode,
  lifeEpisodeLabel,
  lifeResults
} from '../shared/life-trajectory'

import type { RemoteApi } from './api'
import { useAlive, useTask } from './tasks'
import { Media, Notice } from './ui'

const labels = {
  virtual: '虚拟日常',
  milestone: '事项变化',
  reading: '阅读所得',
  result: '已确认成果',
  exchange: '一次交流'
}

export function LifeReading({ reading }: { reading: NonNullable<LifeEpisode['reading_result']> }): ReactElement {
  return (
    <details>
      <summary>阅读笔记</summary>
      <p>{reading.notes}</p>
      <ul>
        {reading.sources.map(source => (
          <li key={source.url}>
            <a href={source.url} rel="noreferrer" target="_blank">
              {source.title || source.url}
            </a>
            {' · '}
            {source.content_kind === 'summary' ? '摘要' : source.excerpted ? '摘录' : '正文'}
          </li>
        ))}
      </ul>
    </details>
  )
}

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
      className="life-result"
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
        <LifeResult
          api={api}
          key={link.kind === 'visual' ? 'version-' + link.versionId : link.kind + link.id}
          link={link}
        />
      ))}
    </>
  )
}

export function LifeEpisodeCard({
  api,
  entry,
  featured = false,
  onPursuit,
  onTopic,
  topic
}: {
  api: RemoteApi
  entry: LifeEpisode
  featured?: boolean
  onPursuit: (id: string) => void
  onTopic: (id: string) => void
  topic: string | null
}): ReactElement {
  const { pursuit_id: pursuitId, topic_id: topicId, topic_title: topicTitle } = entry

  return (
    <article className={featured ? 'life-featured' : 'life-episode'}>
      {featured && <h2>最近的经历</h2>}
      <div className="life-episode-meta">
        <span>{labels[lifeEpisodeLabel(entry)]}</span>
        {featured && <time dateTime={entry.occurred_at}>{formatLifeDay(entry.local_date, 'zh')}</time>}
      </div>
      {entry.title && <h3>{entry.title}</h3>}
      {entry.narrative && <p>{entry.narrative}</p>}
      {entry.reading_result && <LifeReading reading={entry.reading_result} />}
      <LifeResultPreviews api={api} reference={entry.result_ref} />
      <div className="row-actions">
        {topicTitle && topic !== topicId && (
          <button className="subtle" onClick={() => onTopic(topicId)} type="button">
            {topicTitle === entry.title ? '查看这个主题' : topicTitle}
          </button>
        )}
        {pursuitId && (
          <button className="subtle" onClick={() => onPursuit(pursuitId)} type="button">
            查看这件事
          </button>
        )}
      </div>
    </article>
  )
}

export function LifeTimeline({
  api,
  entries,
  onPursuit,
  onTopic,
  topic
}: {
  api: RemoteApi
  entries: readonly LifeEpisode[]
  onPursuit: (id: string) => void
  onTopic: (id: string) => void
  topic: string | null
}): ReactElement {
  return (
    <div className="life-timeline">
      {groupLifeEpisodes(entries).map(({ date, entries }) => (
        <section className="life-episode-day" key={date}>
          <h3>
            <time dateTime={date}>{formatLifeDay(date, 'zh')}</time>
          </h3>
          <ol className="archive-life-results">
            {entries.map(entry => (
              <li key={entry.id}>
                <LifeEpisodeCard api={api} entry={entry} onPursuit={onPursuit} onTopic={onTopic} topic={topic} />
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}
