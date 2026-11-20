import { IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import { newRequestId } from '../shared/request-id'
import {
  isRequestConflictError,
  isStaleDraftError,
  type MotionPlanInputs,
  sameMotionInputs,
  SCENERY_DEFAULT_DESCRIPTION
} from '../shared/visuals'

import { ApiError, type RemoteApi } from './api'
import { AssetResultRecovery, useAssetSubmission } from './asset-submission'
import { AssetCreator, AssetPolicyToggle } from './AssetCreator'
import { copyText, readImage } from './browser'
import { useLibraryPages } from './library-pages'
import { MediaReviewButton } from './MediaReviewButton'
import { usePageScroll, usePageState } from './page-state'
import { requireCurrentRead, useAlive, useTask, useVisiblePolling } from './tasks'
import type { Scene } from './types'
import {
  AssetSteps,
  Empty,
  FileInput,
  focusAfterRender,
  GridSkeleton,
  LibraryCard,
  Media,
  Notice,
  PageHeader
} from './ui'

function sceneStatusText(status: Scene['status']): string {
  switch (status) {
    case 'ready':
      return '已就绪'
    case 'pending':
      return '正在制作'
    case 'description_failed':
    case 'failed':
      return '制作失败'
    case 'cancelled':
      return '已取消'
    default:
      return '待完善'
  }
}

type SceneState = ApiSchemas['SceneStateResponse']

/** 环境动态整理结果与生成时输入的绑定；表单变化后草稿作废，必须重新整理。 */
interface MotionPlan {
  draft: Record<string, unknown> | null
  summary: string
  feasible: boolean
  reason: string
  inputs: MotionPlanInputs
}

export function SceneLibrary({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [state, setState] = useState<SceneState | null>(null)
  const [query, setQuery] = usePageState('scene.query', '')
  const { rows, hasMore, loaded, read, save } = useLibraryPages<Scene>(query, 30)
  // 详情与创建视图随组件生命周期，切 tab 或分区即回一级列表。
  const [selected, setSelected] = useState<Scene | null>(null)
  const [creating, setCreating] = useState(false)
  const [policySaving, setPolicySaving] = useState(false)
  const revision = useRef(0)
  const selectedId = selected?.id
  usePageScroll(`scene.${creating ? 'create' : (selectedId ?? `list.${query}`)}`, loaded)

  const load = useCallback(
    async (append = false) => {
      const version = ++revision.current

      const [library, current, detail] = await Promise.all([
        read(append, async (cursor, limit) => {
          const { scenes: rows, next_cursor } = await api.invoke('companion_scenes.scene_list_route', {
            query: { q: query, cursor, limit }
          })
          return { rows, next_cursor }
        }),
        api.invoke('companion_scenes.read_scene_state'),
        selectedId
          ? api.invoke('companion_scenes.scene_detail_route', { path: { scene_id: selectedId } }).catch(failure => {
              if (failure instanceof ApiError && failure.status === 404) {
                return null
              }
              throw failure
            })
          : null
      ])

      if (alive()) {
        requireCurrentRead(version, revision.current)
        save(library)
        setState(current)
        setSelected(previous => (previous?.id === selectedId ? detail : previous))
      }
    },
    [alive, api, query, read, save, selectedId]
  )

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])
  const pending = Boolean(state?.generation_pending)

  useVisiblePolling(pending, load, runLatest)

  return (
    <>
      {!creating && !selected ? (
        <>
          <PageHeader subtitle="描述一个喜欢的地方，制作成环境素材" title="环境" />
          <div className="library-toolbar">
            <input
              aria-label="搜索环境"
              onChange={event => {
                revision.current++
                setQuery(event.target.value)
              }}
              placeholder="搜索环境"
              type="search"
              value={query}
            />
            <button
              aria-label="创建环境"
              className="primary"
              data-create-asset="scene"
              onClick={() => {
                setCreating(true)
                setSelected(null)
              }}
              type="button"
            >
              <IconPlus aria-hidden size={17} />
              创建环境
            </button>
            <button
              aria-label="刷新环境"
              className="icon-button"
              disabled={busy}
              onClick={() => run(load)}
              type="button"
            >
              <IconRefresh size={20} />
            </button>
          </div>
          <AssetPolicyToggle
            checked={state?.policy === 'llm_may_replace'}
            description="伙伴可以自行制作并收藏新的环境素材。"
            disabled={!state || busy}
            loaded={Boolean(state)}
            onChange={checked => {
              const policy = checked ? 'llm_may_replace' : 'locked'
              void run(async () => {
                setPolicySaving(true)
                try {
                  const next = await api.invoke('companion_scenes.patch_scene_policy', { body: { policy } })
                  if (alive()) {
                    setState(current => (current ? { ...current, policy: next.policy } : current))
                  }
                } finally {
                  if (alive()) {
                    setPolicySaving(false)
                  }
                }
              })
            }}
            saving={policySaving}
            title="允许伙伴自主添置素材"
          />
        </>
      ) : null}
      {state?.generation_pending ? (
        <section className="card">
          <strong>环境素材正在制作</strong>
          <p className="muted">AI 正在准备画面，可以先去聊天，稍后回来查看。</p>
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                const id = state.materials.find(item => item.status === 'pending')?.id

                if (id) {
                  await api.invoke('companion_scenes.post_scene_discard', { path: { scene_id: id } })
                  await load()
                }
              })
            }
            type="button"
          >
            停止制作
          </button>
        </section>
      ) : null}
      {creating ? (
        <AssetCreator
          api={api}
          disabled={pending}
          kind="scene"
          onCheck={load}
          onClose={() => {
            setCreating(false)
            focusAfterRender('[data-create-asset="scene"]')
          }}
          onCreated={created => {
            if (created.kind === 'scene') {
              revision.current++
              setSelected(created.value)
              setCreating(false)
            }
          }}
        />
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      {selected ? (
        <SceneDetail
          api={api}
          key={selected.id}
          onClose={() => {
            setSelected(null)
            focusAfterRender(`[data-card-key="scene-${selected.id}"]`)
          }}
          onUpdate={load}
          scene={selected}
        />
      ) : null}
      {!creating && !selected ? (
        <>
          {!loaded && !rows.length ? <GridSkeleton /> : null}
          <div className="asset-grid">
            {rows.map(row => (
              <LibraryCard
                cardKey={`scene-${row.id}`}
                imageAlt={row.title || '环境'}
                imageUrl={row.url}
                key={row.id}
                meta={sceneStatusText(row.status)}
                onSelect={() => {
                  setSelected(row)
                  setCreating(false)
                }}
                title={row.title || '新的环境'}
              />
            ))}
          </div>
          {loaded && rows.length === 0 && !busy ? (
            <Empty title="给伙伴一个新的地方">描述想去的地方，AI 会为你制作环境素材。</Empty>
          ) : null}
          {hasMore ? (
            <button className="load-more" disabled={busy} onClick={() => void run(() => load(true))} type="button">
              更多环境
            </button>
          ) : null}
        </>
      ) : null}
    </>
  )
}

function SceneDetail({
  api,
  scene,
  onUpdate,
  onClose
}: {
  api: RemoteApi
  scene: Scene
  onUpdate: () => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [title, setTitle] = usePageState(`scene.${scene.id}.title`, scene.title)
  const [description, setDescription] = usePageState(`scene.${scene.id}.description`, scene.description)
  const [motionNotice, setMotionNotice] = useState<string | null>(null)
  const [motionRequest, setMotionRequest] = usePageState<string | null>(`scene.${scene.id}.motionRequest`, null)
  const [motionDescription, setMotionDescription] = usePageState(`scene.${scene.id}.motionDescription`, '')
  const [motionPhase, setMotionPhase] = usePageState<'' | 'dawn' | 'day' | 'dusk' | 'night'>(
    `scene.${scene.id}.motionPhase`,
    ''
  )
  const [motionPlan, setMotionPlan] = usePageState<MotionPlan | null>(`scene.${scene.id}.motionPlan`, null)
  const currentMotionInputs = (): MotionPlanInputs => ({
    description: motionDescription.trim() || SCENERY_DEFAULT_DESCRIPTION,
    sceneId: scene.id,
    phase: motionPhase
  })
  // 草稿只在输入与整理时一致时可用；表单变化后必须重新整理才能提交。
  const motionReady = Boolean(motionPlan && sameMotionInputs(motionPlan.inputs, currentMotionInputs()))
  const discardMotionPlan = (): void => {
    if (motionPlan) {
      setMotionPlan(null)
      setMotionNotice('制作要求已变化，请重新整理后再确认')
    }
  }
  const { check, clearSubmission, reconciled, submit, submitted } = useAssetSubmission(`scene.${scene.id}`, onUpdate)
  const previous = useRef({ title: scene.title, description: scene.description })
  const picker = useRef<HTMLInputElement>(null)
  const canRetryAnalysis =
    ((scene.status === 'description_failed' || scene.status === 'cancelled') && Boolean(scene.url)) ||
    (scene.status === 'failed' && ['review_failed', 'confirmation'].includes(scene.stage)) ||
    (scene.regeneration?.status === 'failed' &&
      (scene.regeneration.stage === 'analyze' || ['review_failed', 'confirmation'].includes(scene.regeneration.stage)))
  const regenerate = () =>
    run(() => submit(() => api.invoke('companion_scenes.post_scene_regenerate', { path: { scene_id: scene.id } })))

  useEffect(() => {
    const old = previous.current
    setTitle(value => (value === old.title ? scene.title : value))
    setDescription(value => (value === old.description ? scene.description : value))
    previous.current = { title: scene.title, description: scene.description }
  }, [scene.title, scene.description, setTitle, setDescription])

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      <AssetSteps step={scene.status === 'ready' ? 3 : 2} />
      <div className="card-title">
        <h1>{scene.title || '新的环境'}</h1>
      </div>
      {scene.url ? (
        <Media
          refresh={() => {
            void run(onUpdate)
          }}
          type="image"
          url={scene.url}
        />
      ) : null}
      {scene.description ? <p className="muted">{scene.description}</p> : null}
      {scene.status === 'pending' ? (
        <p aria-live="polite" className="creation-status">
          正在准备环境素材，可以先去聊天，稍后回来查看。
        </p>
      ) : null}
      <details className="optional-details">
        <summary>修改名称和说明</summary>
        {title !== scene.title || description !== scene.description ? <p className="draft-label">未保存修改</p> : null}
        <label>
          标题
          <input maxLength={80} onChange={event => setTitle(event.target.value)} value={title} />
        </label>
        <label>
          描述
          <textarea
            maxLength={2000}
            onChange={event => setDescription(event.target.value)}
            rows={3}
            value={description}
          />
        </label>
        <button
          disabled={busy || !title.trim() || !description.trim()}
          onClick={() =>
            run(async () => {
              await api.invoke('companion_scenes.scene_edit_route', {
                path: { scene_id: scene.id },
                body: { title, description }
              })
              await onUpdate()
            })
          }
          type="button"
        >
          保存修改
        </button>
        <button
          disabled={busy}
          onClick={() => {
            setTitle(scene.title)
            setDescription(scene.description)
          }}
          type="button"
        >
          取消
        </button>
      </details>
      {scene.status === 'ready' && scene.url ? (
        <>
          <details className="optional-details">
            <summary>制作动态环境</summary>
            <p className="muted">描述想要的动态效果，确认后再开始制作。</p>
            <label>
              动态效果
              <input
                disabled={busy}
                onChange={event => {
                  setMotionDescription(event.target.value)
                  discardMotionPlan()
                }}
                placeholder="例如：窗帘轻轻飘动"
                value={motionDescription}
              />
            </label>
            <label>
              光线时段
              <select
                disabled={busy}
                onChange={event => {
                  setMotionPhase(event.target.value as '' | 'dawn' | 'day' | 'dusk' | 'night')
                  discardMotionPlan()
                }}
                value={motionPhase}
              >
                <option value="">不限</option>
                <option value="dawn">黎明</option>
                <option value="day">白天</option>
                <option value="dusk">黄昏</option>
                <option value="night">夜间</option>
              </select>
            </label>
            {motionPlan && motionReady ? (
              <p aria-live="polite">
                <strong>{motionPlan.summary}</strong>
                {motionPlan.feasible ? null : ` · ${motionPlan.reason}`}
              </p>
            ) : null}
            <button
              disabled={busy || submitted}
              onClick={() =>
                run(async () => {
                  const bound = motionReady ? motionPlan : null

                  if (!bound?.draft) {
                    const inputs = currentMotionInputs()
                    const plan = await api.invoke('companion_visuals.interpret_visual', {
                      body: {
                        description: inputs.description,
                        intent: 'scenery',
                        scene_id: scene.id,
                        day_phase: inputs.phase || null
                      }
                    })
                    if (alive()) {
                      // 与生成时输入绑定；整理期间输入再变化会被时效校验作废。
                      setMotionPlan({
                        draft: plan.draft,
                        summary: plan.summary,
                        feasible: plan.feasible,
                        reason: plan.reason,
                        inputs
                      })
                      setMotionRequest(null)
                      setMotionNotice(null)
                    }
                    return
                  }
                  const requestId = motionRequest ?? newRequestId()
                  setMotionRequest(requestId)
                  try {
                    await api.invoke('companion_visuals.create_visual', {
                      body: { request_id: requestId, draft: bound.draft }
                    })
                  } catch (failure) {
                    if (alive() && failure instanceof ApiError) {
                      if (isStaleDraftError(failure.code)) {
                        setMotionPlan(null)
                        setMotionNotice('素材已变化，请重新整理后再确认')
                        return
                      }

                      if (isRequestConflictError(failure.code)) {
                        setMotionPlan(null)
                        setMotionRequest(null)
                      }
                    }

                    throw failure
                  }
                  if (alive()) {
                    setMotionRequest(null)
                    setMotionPlan(null)
                    setMotionDescription('')
                    // 提示按实际提交的冻结草稿给出，不读取可能已修改的表单值。
                    setMotionNotice(
                      bound.inputs.phase
                        ? '已开始制作该时段的动态环境，可在影像页查看进度。'
                        : '已开始制作动态环境，可在影像页查看进度。'
                    )
                  }
                })
              }
              type="button"
            >
              {motionReady && motionPlan?.draft ? '开始制作' : '整理描述'}
            </button>
          </details>
          {motionNotice ? <p className="muted">{motionNotice}</p> : null}
        </>
      ) : null}
      {scene.review_job_id ? <MediaReviewButton api={api} jobId={scene.review_job_id} onChanged={onUpdate} /> : null}
      {scene.error || scene.regeneration?.error ? <Notice>{scene.error || scene.regeneration?.error}</Notice> : null}
      {scene.can_retry_generation || (scene.regeneration?.status === 'failed' && !canRetryAnalysis) ? (
        <button disabled={busy || submitted} onClick={regenerate} type="button">
          重试制作环境
        </button>
      ) : null}
      {canRetryAnalysis ? (
        <button
          disabled={busy}
          onClick={() =>
            run(async () => {
              await api.invoke('companion_scenes.scene_analyze_route', { path: { scene_id: scene.id } })
              await onUpdate()
            })
          }
          type="button"
        >
          重试图片与描述检查
        </button>
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      {submitted && !busy ? (
        <AssetResultRecovery onCheck={() => run(check)} onContinue={clearSubmission} reconciled={reconciled} />
      ) : null}
      {!scene.url && scene.status !== 'pending' ? (
        <button
          className="primary full"
          disabled={busy || submitted}
          onClick={() => picker.current?.click()}
          type="button"
        >
          上传做好的图片
        </button>
      ) : null}
      {scene.prompt && !scene.url ? (
        <details className="optional-details" open>
          <summary>制作参考</summary>
          <p className="prompt-copy">{scene.prompt}</p>
          <button onClick={() => run(() => copyText(scene.prompt))} type="button">
            复制制作说明
          </button>
        </details>
      ) : null}
      <details className="optional-details">
        <summary>更多操作</summary>
        <div className="button-row">
          <button
            disabled={
              busy ||
              submitted ||
              (scene.status !== 'ready' && !scene.can_retry_generation) ||
              scene.regeneration?.status === 'pending'
            }
            onClick={regenerate}
            type="button"
          >
            重新制作画面
          </button>
          <button disabled={busy || submitted} onClick={() => picker.current?.click()} type="button">
            使用自己的图片
          </button>
          <button
            className="danger"
            disabled={busy}
            onClick={() =>
              run(async () => {
                if (!window.confirm('删除这个环境素材？删除后无法恢复。')) {
                  return
                }
                await api.invoke('companion_scenes.delete_scene_route', { path: { scene_id: scene.id } })

                if (alive()) {
                  onClose()
                }

                await onUpdate()
              })
            }
            type="button"
          >
            删除
          </button>
        </div>
      </details>
      <FileInput
        onFiles={([file]) => {
          if (file) {
            void run(async () => {
              const image = await readImage(file)

              if (alive()) {
                await submit(() =>
                  api.invoke('companion_scenes.create_scene', {
                    body: { source: 'analysis', scene_id: scene.id, image: image.base64 }
                  })
                )
              }
            })
          }
        }}
        ref={picker}
      />
    </section>
  )
}
