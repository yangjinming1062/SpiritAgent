import { IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { VisualDetail, VisualItem, VisualJob, VisualJobCommand, VisualJobState, VisualMediaKind } from '@protocol'

import { visualJobCommands } from '../shared/visuals'

import type { RemoteApi } from './api'
import { ApiError } from './api'
import {
  AssetResultRecovery,
  Empty,
  focusAfterRender,
  LibraryCard,
  Media,
  Notice,
  PageHeader,
  timestamp,
  useAlive,
  useAssetSubmission,
  usePageScroll,
  usePageState,
  useTask,
  useVisiblePolling
} from './ui'
import { VisualAnalysis } from './VisualAnalysis'
import { VisualCreate } from './VisualCreate'

const JOB_STATE_TEXT: Record<VisualJobState, string> = {
  queued: '排队中',
  running: '进行中',
  paused: '已暂停',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  unknown: '状态未知'
}

function durationText(durationMs: number | null): string {
  return durationMs ? `${(durationMs / 1000).toFixed(1)} 秒` : ''
}

const JOB_COMMAND_TEXT: Record<VisualJobCommand, string> = {
  pause: '暂停',
  resume: '继续',
  cancel: '取消',
  retry: '重试'
}

function analysisPending(state: VisualJobState | null | undefined): boolean {
  return state === 'queued' || state === 'running' || state === 'paused'
}

function sourceSummary(item: Pick<VisualItem, 'action_name' | 'outfit_name' | 'scene_title'>): string {
  return [
    item.outfit_name ? `衣柜：${item.outfit_name}` : '',
    item.scene_title ? `环境：${item.scene_title}` : '',
    item.action_name ? `动作：${item.action_name}` : ''
  ]
    .filter(Boolean)
    .join(' · ')
}

function visualJobLabel(job: VisualJob, visualTitle?: string): string {
  if (visualTitle) {
    return visualTitle
  }
  const mediaKind = job.target_kind === 'video' ? '视频' : '图片'
  return job.action_name ? `${job.action_name} · ${mediaKind}` : `影像${mediaKind}`
}

function VisualJobRow({
  api,
  job,
  onChanged,
  visualTitle
}: {
  api: RemoteApi
  job: VisualJob
  onChanged: () => Promise<void>
  visualTitle?: string
}): ReactElement {
  const { run, busy, error } = useTask()

  return (
    <li className="recovery-item">
      <div>
        <strong>{visualJobLabel(job, visualTitle)}</strong>
        <span aria-live="polite" className="pill">
          {JOB_STATE_TEXT[job.state]}
        </span>
        <span className="muted">
          {job.stage} · {timestamp(job.updated_at)}
        </span>
        {job.error ? <small>{job.error}</small> : null}
        {error ? <Notice error={error} /> : null}
      </div>
      {visualJobCommands(job.state).map(command => (
        <button
          disabled={busy}
          key={command}
          onClick={() =>
            run(async () => {
              await api.invoke('companion_visuals.command_visual_job', {
                path: { job_id: job.id },
                body: { command }
              })
              await onChanged()
            })
          }
          type="button"
        >
          {JOB_COMMAND_TEXT[command]}
        </button>
      ))}
    </li>
  )
}

function VisualDetailPanel({
  api,
  currentVisualId,
  onChanged,
  onClose,
  visual
}: {
  api: RemoteApi
  currentVisualId: string | null
  onChanged: () => Promise<void>
  onClose: () => void
  visual: VisualDetail
}): ReactElement {
  const { run, busy, error } = useTask()
  const [feedback, setFeedback] = usePageState(`visual.${visual.id}.feedback`, '')
  const [showOriginal, setShowOriginal] = useState(false)
  const { check, clearSubmission, reconciled, submit, submitted } = useAssetSubmission(`visual.${visual.id}`, onChanged)
  const jobs = visual.jobs
  const pendingAnalysis = analysisPending(visual.analysis?.state)

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      <div className="card-title">
        <h1>{visual.title || '未命名影像'}</h1>
      </div>
      <Media
        refresh={() => {
          void run(onChanged)
        }}
        type={visual.media_kind === 'video' ? 'video' : 'image'}
        url={showOriginal && visual.original_url ? visual.original_url : visual.media_url}
      />
      <p className="muted">
        {visual.media_kind === 'video' ? '视频' : '图片'}
        {durationText(visual.duration_ms) ? ` · ${durationText(visual.duration_ms)}` : ''}
        {currentVisualId === visual.id ? ' · 呈现中' : ''}
        {!pendingAnalysis && !visual.auto_playable ? ' · 此版本不参与自动演出' : ''}
      </p>
      {!pendingAnalysis && !visual.auto_playable ? (
        <div className="muted">
          <p>这个版本已完成并保留，目前不参与常驻轮换或生活演出。你仍可在影像库查看成品与检查记录。</p>
          {visual.snapshot.review_reason ? <p>内容检查：{visual.snapshot.review_reason}</p> : null}
        </div>
      ) : null}
      {visual.description ? <p>{visual.description}</p> : null}
      {visual.original_url && visual.original_url !== visual.media_url && (
        <button onClick={() => setShowOriginal(value => !value)} type="button">
          {showOriginal ? '查看可呈现版本' : '查看原始文件'}
        </button>
      )}
      {visual.original_url ? (
        <button
          disabled={busy}
          onClick={() => {
            if (!window.confirm('删除这份用户上传的影像？')) {
              return
            }
            void run(async () => {
              await api.invoke('companion_visuals.delete_visual', { path: { visual_id: visual.id } })
              await onChanged()
              onClose()
            })
          }}
          type="button"
        >
          删除这份影像
        </button>
      ) : null}
      {visual.analysis && <VisualAnalysis api={api} onChanged={onChanged} visual={visual} />}
      <section className="card">
        <h3>来源素材</h3>
        {(() => {
          const source = visual.snapshot.source

          if (source.kind === 'analysis') {
            return (
              <>
                <p>原始文件：{source.original_filename}</p>
                <p className="muted">原件完整保留，内容识别、素材关联与呈现资格见下方分析结果。</p>
              </>
            )
          }

          if (source.kind === 'scene') {
            return (
              <dl>
                <div>
                  <dt>环境</dt>
                  <dd>{source.scene.title || '未命名素材'}</dd>
                </div>
              </dl>
            )
          }

          return (
            <>
              <dl>
                <div>
                  <dt>衣柜</dt>
                  <dd>{source.outfit.name || '未命名素材'}</dd>
                </div>
                {source.kind === 'cinematic' && (
                  <div>
                    <dt>环境</dt>
                    <dd>{source.scene.title || '未命名素材'}</dd>
                  </div>
                )}
                <div>
                  <dt>动作</dt>
                  <dd>
                    {source.action.name}
                    {source.action.clip_kind === 'loop' ? '（持续动作）' : '（短动作）'}
                    {` · ${source.action.duration_seconds} 秒`}
                  </dd>
                </div>
              </dl>
              {source.action.use_when.length ? (
                <p className="muted">适用情境：{source.action.use_when.join('、')}</p>
              ) : null}
              {source.requirements ? <p className="prompt-copy">{source.requirements}</p> : null}
            </>
          )
        })()}
      </section>
      {jobs.length ? (
        <section className="card">
          <h3>制作工作项</h3>
          <ul className="visual-list">
            {jobs.map(job => (
              <VisualJobRow api={api} job={job} key={job.id} onChanged={onChanged} />
            ))}
          </ul>
        </section>
      ) : null}
      {visual.snapshot.source.kind !== 'analysis' && (
        <details className="optional-details">
          <summary>重做</summary>
          <p className="muted">按原来的素材组合重新制作，可以附上修改要求。不需要电脑在线。</p>
          <textarea
            aria-label="修改要求"
            disabled={busy || submitted}
            maxLength={2000}
            onChange={event => setFeedback(event.target.value)}
            placeholder="例如：背景换成傍晚的光线，动作保持不变"
            rows={3}
            value={feedback}
          />
          <div className="button-row">
            <button
              disabled={busy || submitted}
              onClick={() =>
                run(() =>
                  submit(async () => {
                    await api.invoke('companion_visuals.regenerate_visual', {
                      path: { visual_id: visual.id },
                      body: { expected_version: visual.version, feedback: feedback.trim() || null }
                    })
                    setFeedback('')
                    await onChanged()
                    clearSubmission()
                  })
                )
              }
              type="button"
            >
              提交重做
            </button>
          </div>
        </details>
      )}
      {error ? <Notice>{error}</Notice> : null}
      {submitted && !busy ? (
        <AssetResultRecovery onCheck={() => run(check)} onContinue={clearSubmission} reconciled={reconciled} />
      ) : null}
    </section>
  )
}

export function Visuals({
  api,
  onBack,
  refresh
}: {
  api: RemoteApi
  onBack: () => void
  refresh: number
}): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [rows, setRows] = useState<VisualItem[]>([])
  const [hasMore, setHasMore] = useState(false)
  const nextCursor = useRef<string | null>(null)
  const [jobs, setJobs] = useState<VisualJob[]>([])
  const [currentVisualId, setCurrentVisualId] = useState<string | null>(null)
  const [selected, setSelected] = usePageState<VisualDetail | null>('visuals.selected', null)
  const [query, setQuery] = usePageState('visuals.query', '')
  const [mediaKind, setMediaKind] = usePageState<'all' | VisualMediaKind>('visuals.mediaKind', 'all')
  const [source, setSource] = usePageState('visuals.source', '')
  const [loaded, setLoaded] = useState(false)
  const revision = useRef(0)
  const selectedId = selected?.id
  usePageScroll(`visuals.${selectedId ?? `list.${query}`}`, loaded)

  const load = useCallback(
    async (append = false) => {
      const version = ++revision.current
      const [list, jobList, presentation, detail] = await Promise.all([
        api.invoke('companion_visuals.list_visuals', {
          query: {
            limit: 30,
            media_kind: mediaKind === 'all' ? undefined : mediaKind,
            cursor: append ? (nextCursor.current ?? undefined) : undefined,
            query: query.trim() || undefined,
            source: source.trim() || undefined
          }
        }),
        api.invoke('companion_visuals.list_visual_jobs', {}),
        api.invoke('companion_visuals.get_presentation', {}),
        selectedId
          ? api.invoke('companion_visuals.get_visual', { path: { visual_id: selectedId } }).catch(failure => {
              if (failure instanceof ApiError && failure.status === 404) {
                return null
              }
              throw failure
            })
          : null
      ])

      if (alive() && revision.current === version) {
        setRows(previous =>
          append
            ? [...previous, ...list.visuals.filter(item => !previous.some(existing => existing.id === item.id))]
            : list.visuals
        )
        setHasMore(list.has_more)
        nextCursor.current = list.next_cursor
        setJobs(jobList.jobs)
        setCurrentVisualId(presentation.current_visual?.id ?? null)
        setLoaded(true)
        setSelected(previous => (previous?.id === selectedId ? detail : previous))
      }
    },
    [alive, api, mediaKind, query, selectedId, setSelected, source]
  )

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  const producing =
    jobs.some(job => job.state === 'queued' || job.state === 'running') ||
    rows.some(row => row.analysis_state === 'queued' || row.analysis_state === 'running') ||
    selected?.analysis?.state === 'queued' ||
    selected?.analysis?.state === 'running'
  useVisiblePolling(producing, load, runLatest)

  return (
    <>
      {!selected ? (
        <>
          <button className="back-link" onClick={onBack} type="button">
            ← 返回伙伴
          </button>
          <PageHeader subtitle="创建、上传与收藏你和伙伴的影像" title="影像" />
          <VisualCreate
            api={api}
            onDone={async visualId => {
              setQuery('')
              setSource('')
              setMediaKind('all')
              if (visualId) {
                const detail = await api.invoke('companion_visuals.get_visual', { path: { visual_id: visualId } })
                if (alive()) {
                  setSelected(detail)
                }
              }
              await load()
            }}
          />
          <div className="library-toolbar">
            <input
              aria-label="按名称筛选影像"
              onChange={event => {
                revision.current++
                setRows([])
                nextCursor.current = null
                setQuery(event.target.value)
              }}
              placeholder="按名称筛选"
              type="search"
              value={query}
            />
            <button
              aria-label="刷新影像"
              className="icon-button"
              disabled={busy}
              onClick={() => run(load)}
              type="button"
            >
              <IconRefresh size={20} />
            </button>
          </div>
          <details className="filter-details">
            <summary>更多筛选</summary>
            <div className="date-filter">
              <label>
                媒体类型
                <select
                  onChange={event => {
                    revision.current++
                    nextCursor.current = null
                    setMediaKind(event.target.value === 'all' ? 'all' : (event.target.value as VisualMediaKind))
                  }}
                  value={mediaKind}
                >
                  <option value="all">全部</option>
                  <option value="image">图片</option>
                  <option value="video">视频</option>
                </select>
              </label>
              <label>
                来源素材
                <input
                  onChange={event => {
                    revision.current++
                    nextCursor.current = null
                    setSource(event.target.value)
                  }}
                  placeholder="衣柜、环境或动作名称"
                  type="search"
                  value={source}
                />
              </label>
            </div>
          </details>
          {jobs.length ? (
            <section className="card">
              <h3>制作工作项</h3>
              <ul className="visual-list">
                {jobs.map(job => (
                  <VisualJobRow
                    api={api}
                    job={job}
                    key={job.id}
                    onChanged={load}
                    visualTitle={rows.find(row => row.id === (job.target_visual_id ?? job.result_visual_id))?.title}
                  />
                ))}
              </ul>
            </section>
          ) : null}
          {error ? <Notice>{error}</Notice> : null}
          <div className="asset-grid">
            {rows.map(row => (
              <LibraryCard
                active={currentVisualId === row.id}
                cardKey={`visual-${row.id}`}
                imageAlt={row.title || '影像'}
                imageUrl={row.poster_url || row.media_url}
                key={row.id}
                meta={`${row.media_kind === 'video' ? '视频' : '图片'}${
                  durationText(row.duration_ms) ? ` · ${durationText(row.duration_ms)}` : ''
                }${currentVisualId === row.id ? ' · 呈现中' : ''}${
                  analysisPending(row.analysis_state)
                    ? ' · 等待分析'
                    : !row.auto_playable
                      ? ' · 此版本不参与自动演出'
                      : ''
                }`}
                onSelect={() => {
                  void run(async () => {
                    const detail = await api.invoke('companion_visuals.get_visual', {
                      path: { visual_id: row.id }
                    })
                    if (alive()) {
                      setSelected(detail)
                    }
                  })
                }}
                submeta={sourceSummary(row) || undefined}
                title={row.title || '未命名影像'}
              />
            ))}
          </div>
          {loaded && rows.length === 0 && !busy ? (
            <Empty title="还没有影像">选择已有素材创建影像，或上传喜欢的图片与视频，成品会保存在这里。</Empty>
          ) : null}
          {hasMore ? (
            <button className="load-more" disabled={busy} onClick={() => void run(() => load(true))} type="button">
              更多影像
            </button>
          ) : null}
        </>
      ) : (
        <VisualDetailPanel
          api={api}
          currentVisualId={currentVisualId}
          onChanged={load}
          onClose={() => {
            setSelected(null)
            focusAfterRender(`[data-card-key="visual-${selected.id}"]`)
          }}
          visual={selected}
        />
      )}
    </>
  )
}
