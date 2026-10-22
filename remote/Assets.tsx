import { IconPhoto, IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiInput, ApiOperationKey } from '../shared/api'
import type { ApiSchemas } from '../shared/api.generated'
import { uiCopy } from '../shared/ui-copy'

import type { RemoteApi } from './api'
import { ApiError, readImage } from './api'
import type { Outfit, Scene } from './types'
import {
  Empty,
  focusAfterRender,
  ImageInput,
  Media,
  Notice,
  PageHeader,
  useAlive,
  usePageScroll,
  usePageState,
  useTask,
  useVisiblePolling
} from './ui'

type SceneState = ApiSchemas['SceneStateResponse']

function useAssetSubmission(key: string, onCheck: () => Promise<void>) {
  const alive = useAlive()
  const [submitted, setSubmitted] = usePageState(`${key}.submitted`, false)
  const [reconciled, setReconciled] = useState(false)

  const submit = async <Result,>(task: () => Promise<Result>): Promise<Result> => {
    setSubmitted(true)
    setReconciled(false)
    try {
      return await task()
    } catch (failure) {
      if (alive() && failure instanceof ApiError && failure.status !== null && failure.status < 500) {
        setSubmitted(false)
      }
      throw failure
    }
  }
  const check = async () => {
    await onCheck()
    if (alive()) {
      setReconciled(true)
    }
  }

  return { check, clearSubmission: () => setSubmitted(false), reconciled, submit, submitted }
}

function AssetResultRecovery({
  onCheck,
  onContinue,
  reconciled
}: {
  onCheck: () => Promise<void>
  onContinue: () => void
  reconciled: boolean
}): ReactElement {
  return (
    <div className="creation-status">
      <p>上次制作的结果尚未确认。请先刷新，核对原来的结果。</p>
      <div className="button-row">
        <button onClick={onCheck} type="button">
          刷新结果
        </button>
        <button disabled={!reconciled} onClick={onContinue} type="button">
          已核对，继续调整
        </button>
      </div>
    </div>
  )
}

function AssetSteps({
  kind,
  step,
  upload = false
}: {
  kind: 'scene' | 'outfit'
  step: number
  upload?: boolean
}): ReactElement {
  const labels = [upload ? '选择图片' : '描述想法', '预览确认', kind === 'scene' ? '使用背景' : '穿上新衣']

  return (
    <ol aria-label="制作步骤" className="creation-steps">
      {labels.map((label, index) => (
        <li aria-current={step === index + 1 ? 'step' : undefined} key={label}>
          <span>{index + 1}</span>
          {label}
        </li>
      ))}
    </ol>
  )
}

function AssetPolicyToggle({
  checked,
  description,
  disabled,
  loaded,
  onChange,
  saving,
  title
}: {
  checked: boolean
  description: string
  disabled: boolean
  loaded: boolean
  onChange: (checked: boolean) => void
  saving: boolean
  title: string
}): ReactElement {
  return (
    <label className="policy-toggle">
      <input checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} type="checkbox" />
      <span>
        <strong>{title}</strong>
        <small>{description}</small>
        <small aria-live="polite">
          {saving ? uiCopy.saving : !loaded ? '尚未加载' : checked ? '已开启' : '已关闭'}
        </small>
      </span>
    </label>
  )
}

function AssetCard({
  actionDisabled,
  actionLabel,
  active,
  id,
  imageAlt,
  imageUrl,
  kind,
  onAction,
  onPreview,
  status,
  title
}: {
  actionDisabled: boolean
  actionLabel: string
  active: boolean
  id: number
  imageAlt: string
  imageUrl: string
  kind: 'scene' | 'outfit'
  onAction: () => void
  onPreview: () => void
  status: string
  title: string
}): ReactElement {
  return (
    <article className={`asset-card ${active ? 'active' : ''}`}>
      <button
        className="asset-card-preview"
        data-outfit-id={kind === 'outfit' ? id : undefined}
        data-scene-id={kind === 'scene' ? id : undefined}
        onClick={onPreview}
        type="button"
      >
        {imageUrl ? <img alt={imageAlt} loading="lazy" src={imageUrl} /> : <div className="asset-placeholder">✦</div>}
        <div>
          <strong>{title}</strong>
          <small>{status}</small>
        </div>
      </button>
      <button className="asset-card-action" disabled={actionDisabled} onClick={onAction} type="button">
        {actionLabel}
      </button>
    </article>
  )
}

export function Assets({
  api,
  kind,
  refresh
}: {
  api: RemoteApi
  kind: 'scene' | 'outfit'
  refresh: number
}): ReactElement {
  return kind === 'scene' ? <SceneLibrary api={api} refresh={refresh} /> : <Wardrobe api={api} refresh={refresh} />
}

function AssetCreator({
  api,
  disabled = false,
  kind,
  onCheck,
  onClose,
  onCreated
}: {
  api: RemoteApi
  disabled?: boolean
  kind: 'scene' | 'outfit'
  onCheck: () => Promise<void>
  onClose: () => void
  onCreated: (id: number) => Promise<void>
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [description, setDescription] = usePageState(`${kind}.description`, '')
  const [source, setSource] = usePageState<'ai' | 'upload'>(`${kind}.source`, 'ai')
  const [image, setImage] = usePageState<Awaited<ReturnType<typeof readImage>> | null>(`${kind}.image`, null, false)
  const [prompt, setPrompt] = usePageState(`${kind}.prompt`, '')
  const { check, clearSubmission, reconciled, submit, submitted } = useAssetSubmission(kind, onCheck)
  const picker = useRef<HTMLInputElement>(null)
  const scene = kind === 'scene'
  const label = scene ? '背景' : '衣服'
  const imageBody = image ? { image: image.base64, content_type: image.type } : {}

  return (
    <section className="creation-screen">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      <h1>{source === 'ai' ? `让 AI 帮你设计${label}` : `使用自己的${label}图片`}</h1>
      <p className="muted">
        {source === 'ai' ? '用日常的话说说想法，完成后可以先预览。' : '上传已经做好的图片，准备完成后再选择是否使用。'}
      </p>
      <AssetSteps kind={kind} step={1} upload={source === 'upload'} />
      {source === 'ai' || !scene ? (
        <label className="field-label">
          {source === 'upload' ? '补充说明（可选）' : scene ? '想去怎样的地方？' : '想给伙伴穿什么？'}
          <textarea
            disabled={busy || submitted}
            maxLength={500}
            onChange={event => setDescription(event.target.value)}
            placeholder={
              scene ? '例如：午后阳光照进有绿植的温暖书房' : '例如：米白色毛衣搭配浅蓝牛仔裤，轻松日常的感觉'
            }
            rows={5}
            value={description}
          />
        </label>
      ) : null}
      {source === 'upload' ? (
        <div className="upload-area">
          {image ? (
            <img alt="准备上传的图片" className="upload-preview" src={image.url} />
          ) : (
            <IconPhoto aria-hidden size={34} stroke={1.4} />
          )}
          <button disabled={busy} onClick={() => picker.current?.click()} type="button">
            {image ? '换一张图片' : '选择图片'}
          </button>
          <p className="muted">PNG、JPEG、WebP 或 GIF，最大 6 MB</p>
        </div>
      ) : (
        <details className="optional-details">
          <summary>{image ? '已添加参考图片' : '添加参考图片（可选）'}</summary>
          <p className="muted">AI 会参考图片中的风格和细节。</p>
          {image ? <img alt="制作参考" className="reference-image" src={image.url} /> : null}
          <div className="button-row">
            <button disabled={busy} onClick={() => picker.current?.click()} type="button">
              {image ? '更换参考图' : '选择参考图'}
            </button>
            {image ? (
              <button disabled={busy} onClick={() => setImage(null)} type="button">
                移除参考图
              </button>
            ) : null}
          </div>
        </details>
      )}
      {error ? <Notice>{error}</Notice> : null}
      {submitted ? (
        <div aria-live="polite" className="creation-status">
          <strong>{busy ? '正在准备画面' : '请先查看制作结果'}</strong>
          <p>已受理的制作会继续运行。请先核对列表中的结果，避免重复制作。</p>
          <button className="subtle" onClick={onClose} type="button">
            返回查看结果
          </button>
          {!busy ? (
            <div className="button-row">
              <button onClick={() => run(check)} type="button">
                刷新制作列表
              </button>
              <button disabled={!reconciled} onClick={clearSubmission} type="button">
                已核对结果，开始新的制作
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="creation-footer">
        <button
          className="primary full"
          disabled={
            busy || disabled || submitted || (source === 'upload' ? !image : !scene && !description.trim() && !image)
          }
          onClick={() =>
            run(async () => {
              const result = await submit(async () => {
                if (source === 'upload') {
                  if (!image) {
                    return
                  }
                  const imported = { source: 'import' as const, image: image.base64, content_type: image.type }
                  return scene
                    ? api.invoke('companion_scenes.create_scene', { body: imported })
                    : api.invoke('companion.post_outfit', {
                        body: { ...imported, description: description.trim() || undefined }
                      })
                }
                return scene
                  ? api.invoke('companion_scenes.create_scene', {
                      body: { source: 'generate', notes: description.trim() || undefined, ...imageBody }
                    })
                  : api.invoke('companion.post_outfit', {
                      body: { source: 'generate', description: description.trim() || undefined, ...imageBody }
                    })
              })
              if (result && alive()) {
                setDescription('')
                setImage(null)
                setPrompt('')
                setSource('ai')
                await onCreated(result.id)
                clearSubmission()
              }
            })
          }
          type="button"
        >
          {busy ? '正在准备…' : source === 'ai' ? `生成${label}` : '上传并预览'}
        </button>
        <button
          className="text-button"
          disabled={busy || submitted}
          onClick={() => setSource(source === 'ai' ? 'upload' : 'ai')}
          type="button"
        >
          {source === 'ai' ? '使用自己的图片' : '让 AI 帮我制作'}
        </button>
      </div>
      {source === 'upload' ? (
        <details className="optional-details">
          <summary>需要在其他工具中制作？</summary>
          <p className="muted">可以准备一份制作说明，完成后回到这里上传成品。</p>
          {scene ? (
            <label className="field-label">
              想要怎样的画面？
              <textarea
                disabled={busy || submitted}
                maxLength={500}
                onChange={event => setDescription(event.target.value)}
                rows={3}
                value={description}
              />
            </label>
          ) : null}
          <button
            disabled={busy || submitted}
            onClick={() =>
              run(async () => {
                if (scene) {
                  const result = await submit(() =>
                    api.invoke('companion_scenes.post_scene_prompt', {
                      body: { notes: description.trim() || undefined }
                    })
                  )
                  if (alive()) {
                    setPrompt(result.prompt)
                    setSource('ai')
                    await onCreated(result.id)
                    clearSubmission()
                  }
                } else {
                  const result = await api.invoke('companion.post_outfit_prompt', {
                    body: { description: description.trim() || undefined, ...imageBody }
                  })
                  if (alive()) {
                    setPrompt(result.prompt)
                  }
                }
              })
            }
            type="button"
          >
            准备制作说明
          </button>
          {prompt ? (
            <>
              <p className="prompt-copy">{prompt}</p>
              <button
                onClick={() =>
                  run(async () => {
                    await navigator.clipboard.writeText(prompt)
                  })
                }
                type="button"
              >
                复制说明
              </button>
            </>
          ) : null}
        </details>
      ) : null}
      <ImageInput
        onFiles={([file]) => {
          if (file) {
            void run(async () => {
              const next = await readImage(file)
              if (alive()) {
                setImage(next)
              }
            })
          }
        }}
        ref={picker}
      />
    </section>
  )
}

function SceneLibrary({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [rows, setRows] = useState<Scene[]>([])
  const [state, setState] = useState<SceneState | null>(null)
  const [query, setQuery] = usePageState('scene.query', '')
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = usePageState<Scene | null>('scene.selected', null)
  const [creating, setCreating] = usePageState('scene.creating', false)
  const [loaded, setLoaded] = useState(false)
  const [policySaving, setPolicySaving] = useState(false)
  const [browseLimit, setBrowseLimit] = usePageState('scene.browseLimit', 30)
  const revision = useRef(0)
  const selectedId = selected?.id
  usePageScroll(`scene.${creating ? 'create' : (selectedId ?? `list.${query}`)}`, loaded)

  const load = useCallback(async () => {
    const version = ++revision.current

    const [library, current, detail] = await Promise.all([
      Promise.all(
        Array.from({ length: Math.ceil(browseLimit / 100) }, (_, index) =>
          api.invoke('companion_scenes.scene_list_route', {
            query: { q: query, offset: index * 100, limit: Math.min(100, browseLimit - index * 100) }
          })
        )
      ).then(pages => ({
        scenes: [...new Map(pages.flatMap(page => page.scenes).map(scene => [scene.id, scene])).values()],
        total: pages[0]?.total ?? 0
      })),
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

    if (alive() && revision.current === version) {
      setRows(library.scenes)
      setTotal(library.total)
      setState(current)
      setLoaded(true)
      setSelected(previous => (previous?.id === selectedId ? detail : previous))
    }
  }, [alive, api, browseLimit, query, selectedId, setSelected])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])
  const pending = Boolean(state?.pending || state?.regenerating)

  useVisiblePolling(pending, load, runLatest)

  return (
    <>
      {!creating && !selected ? (
        <>
          <PageHeader subtitle="描述一个喜欢的地方，让 AI 画出来" title="窗口背景" />
          <div className="library-toolbar">
            <input
              aria-label="搜索背景"
              onChange={event => {
                revision.current++
                setRows([])
                setBrowseLimit(30)
                setQuery(event.target.value)
              }}
              placeholder="搜索背景"
              type="search"
              value={query}
            />
            <button
              aria-label="创建背景"
              className="primary"
              data-create-asset="scene"
              onClick={() => {
                setCreating(true)
                setSelected(null)
              }}
              type="button"
            >
              <IconPlus aria-hidden size={17} />
              创建背景
            </button>
            <button
              aria-label="刷新背景"
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
            description="伙伴可以自行创建并切换背景。"
            disabled={!state || busy}
            loaded={Boolean(state)}
            onChange={checked => {
              const policy = checked ? 'llm_may_replace' : 'locked'
              void run(async () => {
                setPolicySaving(true)
                try {
                  await api.invoke('companion_scenes.patch_scene_policy', { body: { policy } })
                  await load()
                } finally {
                  if (alive()) {
                    setPolicySaving(false)
                  }
                }
              })
            }}
            saving={policySaving}
            title="允许伙伴自主更换背景"
          />
          {state?.active ? <p className="current-asset">当前背景：{state.active.title || '未命名背景'}</p> : null}
        </>
      ) : null}
      {state?.pending || state?.regenerating ? (
        <section className="card">
          <strong>背景正在制作</strong>
          <p className="muted">AI 正在准备画面，可以先去聊天，稍后回来查看。</p>
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                const id = state.pending?.id ?? state.regenerating?.id

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
          onCreated={async id => {
            const row = await api.invoke('companion_scenes.scene_detail_route', { path: { scene_id: id } })
            if (alive()) {
              setSelected(row)
              setCreating(false)
            }
            await load()
          }}
        />
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      {selected ? (
        <SceneDetail
          api={api}
          current={selected.id === state?.active?.id}
          key={selected.id}
          onClose={() => {
            setSelected(null)
            focusAfterRender(`[data-scene-id="${selected.id}"]`)
          }}
          onUpdate={load}
          scene={selected}
        />
      ) : null}
      {!creating && !selected ? (
        <>
          <div className="asset-grid">
            {rows.map(row => (
              <AssetCard
                actionDisabled={busy || state?.active?.id === row.id}
                actionLabel={
                  state?.active?.id === row.id ? '正在使用' : row.status === 'ready' ? '使用这个背景' : '继续制作'
                }
                active={state?.active?.id === row.id}
                id={row.id}
                imageAlt={row.title || '背景'}
                imageUrl={row.url}
                key={row.id}
                kind="scene"
                onAction={() => {
                  if (row.status !== 'ready' || !row.url) {
                    setSelected(row)
                    return
                  }
                  void run(async () => {
                    const image = new Image()
                    image.src = row.url
                    await image.decode()
                    if (!alive()) {
                      return
                    }
                    await api.invoke('companion_scenes.post_scene_activate', { body: { scene_id: row.id } })
                    await load()
                  })
                }}
                onPreview={() => {
                  setSelected(row)
                  setCreating(false)
                }}
                status={
                  state?.active?.id === row.id
                    ? '正在使用'
                    : row.status === 'ready'
                      ? '已就绪'
                      : row.status === 'pending'
                        ? '正在制作'
                        : row.status === 'description_failed' || row.status === 'failed'
                          ? '制作失败'
                          : row.status === 'cancelled'
                            ? '已取消'
                            : '待完善'
                }
                title={row.title || '新的背景'}
              />
            ))}
          </div>
          {loaded && rows.length === 0 && !busy ? (
            <Empty title="给伙伴一个新的地方">描述想去的地方，AI 会为你制作背景。</Empty>
          ) : null}
          {rows.length < total ? (
            <button
              className="load-more"
              disabled={busy}
              onClick={() => setBrowseLimit(Math.min(total, rows.length + 30))}
              type="button"
            >
              更多背景
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
  current,
  onUpdate,
  onClose
}: {
  api: RemoteApi
  scene: Scene
  current: boolean
  onUpdate: () => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [title, setTitle] = usePageState(`scene.${scene.id}.title`, scene.title)
  const [description, setDescription] = usePageState(`scene.${scene.id}.description`, scene.description)
  const { check, clearSubmission, reconciled, submit, submitted } = useAssetSubmission(`scene.${scene.id}`, onUpdate)
  const previous = useRef({ title: scene.title, description: scene.description })
  const [loadedImage, setLoadedImage] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const canRetryAnalysis =
    ((scene.status === 'description_failed' || scene.status === 'cancelled') && Boolean(scene.url)) ||
    (scene.status === 'failed' && scene.stage === 'review_failed') ||
    (scene.regeneration?.status === 'failed' &&
      (scene.regeneration.stage === 'analyze' || scene.regeneration.stage === 'review_failed'))
  const regenerate = () =>
    run(() =>
      submit(async () => {
        await api.invoke('companion_scenes.post_scene_regenerate', { path: { scene_id: scene.id } })
        await onUpdate()
        clearSubmission()
      })
    )

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
      {!current ? <AssetSteps kind="scene" step={scene.status === 'ready' ? 3 : 2} /> : null}
      <div className="card-title">
        <h1>{scene.title || '新的背景'}</h1>
      </div>
      {scene.url ? (
        <Media
          onReady={() => setLoadedImage(scene.url)}
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
          正在准备背景，可以先去聊天，稍后回来查看。
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
      {scene.error || scene.regeneration?.error ? <Notice>{scene.error || scene.regeneration?.error}</Notice> : null}
      {scene.can_retry_generation || (scene.regeneration?.status === 'failed' && !canRetryAnalysis) ? (
        <button disabled={busy || submitted} onClick={regenerate} type="button">
          重试制作背景
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
      {scene.url || scene.status === 'pending' ? (
        <div className="creation-footer">
          <button
            className="primary full"
            disabled={current || busy || scene.status !== 'ready' || loadedImage !== scene.url}
            onClick={() =>
              run(async () => {
                await api.invoke('companion_scenes.post_scene_activate', { body: { scene_id: scene.id } })
                await onUpdate()
              })
            }
            type="button"
          >
            {current ? '正在使用' : scene.status === 'pending' ? '正在制作…' : '使用此背景'}
          </button>
        </div>
      ) : null}
      {!scene.url && scene.status !== 'pending' ? (
        <button className="primary full" disabled={busy} onClick={() => picker.current?.click()} type="button">
          上传做好的图片
        </button>
      ) : null}
      {scene.prompt && !scene.url ? (
        <details className="optional-details" open>
          <summary>制作参考</summary>
          <p className="prompt-copy">{scene.prompt}</p>
          <button
            onClick={() =>
              run(async () => {
                await navigator.clipboard.writeText(scene.prompt)
              })
            }
            type="button"
          >
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
          <button disabled={busy} onClick={() => picker.current?.click()} type="button">
            使用自己的图片
          </button>
          <button
            className="danger"
            disabled={busy || current}
            onClick={() =>
              run(async () => {
                if (!window.confirm('删除这个背景？删除后无法恢复。')) {
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
      <ImageInput
        onFiles={([file]) => {
          if (file) {
            void run(async () => {
              const image = await readImage(file)

              if (alive()) {
                await api.invoke('companion_scenes.create_scene', {
                  body: { source: 'import', scene_id: scene.id, image: image.base64, content_type: image.type }
                })
                await onUpdate()
              }
            })
          }
        }}
        ref={picker}
      />
    </section>
  )
}

function Wardrobe({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [outfits, setOutfits] = useState<Outfit[]>([])
  const [policy, setPolicy] = useState<string | null>(null)
  const [policySaving, setPolicySaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [creating, setCreating] = usePageState('outfit.creating', false)
  const [selectedId, setSelectedId] = usePageState<number | null>('outfit.selected', null)
  const revision = useRef(0)

  const load = useCallback(async () => {
    const current = ++revision.current
    const wardrobe = await api.invoke('companion.get_outfits')

    if (alive() && current === revision.current) {
      setOutfits(wardrobe.outfits)
      setPolicy(wardrobe.policy)
      setLoaded(true)
    }
  }, [api, alive])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  const pending = outfits.some(
    outfit => outfit.description_status === 'pending' || outfit.description_status === 'processing'
  )

  useVisiblePolling(pending, load, runLatest)

  const selected = outfits.find(outfit => outfit.id === selectedId)
  usePageScroll(`outfit.${creating ? 'create' : (selected?.id ?? 'list')}`, loaded)

  return (
    <>
      {!creating && !selected ? (
        <>
          <PageHeader subtitle="描述喜欢的颜色和风格，AI 帮你制作" title="衣柜" />
          <div className="library-toolbar">
            <h3>伙伴的衣柜</h3>
            <button
              aria-label="制作新外观"
              className="primary"
              data-create-asset="outfit"
              onClick={() => {
                setCreating(true)
                setSelectedId(null)
              }}
              type="button"
            >
              <IconPlus aria-hidden size={17} />
              设计新衣服
            </button>
            <button
              aria-label="刷新衣柜"
              className="icon-button"
              disabled={busy}
              onClick={() => run(load)}
              type="button"
            >
              <IconRefresh size={20} />
            </button>
          </div>
          <AssetPolicyToggle
            checked={policy === 'llm_may_replace'}
            description="伙伴可以夜间挑选已有衣服，也可以设计新衣服。"
            disabled={busy || !loaded}
            loaded={loaded}
            onChange={checked => {
              const value = checked ? 'llm_may_replace' : 'locked'
              void run(async () => {
                setPolicySaving(true)
                try {
                  await api.invoke('companion.patch_outfit_policy', { body: { policy: value } })
                  await load()
                } finally {
                  if (alive()) {
                    setPolicySaving(false)
                  }
                }
              })
            }}
            saving={policySaving}
            title="允许伙伴自主换装"
          />
        </>
      ) : null}
      {creating ? (
        <AssetCreator
          api={api}
          kind="outfit"
          onCheck={load}
          onClose={() => {
            setCreating(false)
            focusAfterRender('[data-create-asset="outfit"]')
          }}
          onCreated={async id => {
            if (alive()) {
              setSelectedId(id)
              setCreating(false)
            }
            await load()
          }}
        />
      ) : null}
      {error ? (
        <Notice error={error}>
          <button disabled={busy} onClick={() => run(load)} type="button">
            查看制作结果
          </button>
        </Notice>
      ) : null}
      {selected ? (
        <OutfitDetail
          api={api}
          key={selected.id}
          onClose={() => {
            setSelectedId(null)
            focusAfterRender(`[data-outfit-id="${selected.id}"]`)
          }}
          onUpdate={load}
          outfit={selected}
        />
      ) : null}
      {!creating && !selected ? (
        <>
          <div className="asset-grid outfits">
            {outfits.map(outfit => {
              return (
                <AssetCard
                  actionDisabled={busy || outfit.active || outfit.status === 'expired'}
                  actionLabel={outfit.active ? '正在穿着' : outfit.status === 'draft' ? '继续制作' : '穿这套'}
                  active={outfit.active}
                  id={outfit.id}
                  imageAlt={outfit.name}
                  imageUrl={outfit.fullbody_url}
                  key={outfit.id}
                  kind="outfit"
                  onAction={() => {
                    if (outfit.status !== 'ready') {
                      setSelectedId(outfit.id)
                      return
                    }
                    void run(async () => {
                      await api.invoke('companion.put_outfit_selection', { body: { outfit_id: outfit.id } })
                      await load()
                    })
                  }}
                  onPreview={() => {
                    setSelectedId(outfit.id)
                    setCreating(false)
                  }}
                  status={
                    outfit.active
                      ? '正在穿着'
                      : outfit.status === 'ready'
                        ? '已入柜'
                        : outfit.status === 'draft'
                          ? '待确认'
                          : '已过期'
                  }
                  title={outfit.name || '新外观'}
                />
              )
            })}
          </div>
          {loaded && outfits.length === 0 && !busy ? (
            <Empty title="为伙伴设计一套新衣服">描述喜欢的颜色和风格，AI 会帮你画出来。</Empty>
          ) : null}
        </>
      ) : null}
    </>
  )
}

function OutfitDetail({
  api,
  outfit,
  onUpdate,
  onClose
}: {
  api: RemoteApi
  outfit: Outfit
  onUpdate: () => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [feedback, setFeedback] = usePageState(`outfit.${outfit.id}.feedback`, '')
  const { check, clearSubmission, reconciled, submit, submitted } = useAssetSubmission(`outfit.${outfit.id}`, onUpdate)
  const [loadedImage, setLoadedImage] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const produce = <K extends ApiOperationKey>(operation: K, input: ApiInput<K>) =>
    run(() =>
      submit(async () => {
        await api.invoke(operation, input)
        await onUpdate()
        clearSubmission()
        setFeedback('')
      })
    )
  const regenerate = (mode: 'edit' | 'regenerate') =>
    produce('companion.post_outfit_regenerate', {
      path: { outfit_id: outfit.id },
      body: { source: 'generate', feedback: feedback.trim() || undefined, mode }
    })

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      {!outfit.active ? <AssetSteps kind="outfit" step={outfit.status === 'ready' ? 3 : 2} /> : null}
      <div className="card-title">
        <h1>{outfit.name || '新的衣服'}</h1>
      </div>
      {outfit.fullbody_url ? (
        <Media
          onReady={() => setLoadedImage(outfit.fullbody_url)}
          refresh={() => {
            void run(onUpdate)
          }}
          type="image"
          url={outfit.fullbody_url}
        />
      ) : null}
      <p>{outfit.description}</p>
      {outfit.description_error ? <Notice>{outfit.description_error}</Notice> : null}
      {error ? <Notice>{error}</Notice> : null}
      {submitted && !busy ? (
        <AssetResultRecovery onCheck={() => run(check)} onContinue={clearSubmission} reconciled={reconciled} />
      ) : null}
      <div className="creation-footer">
        {outfit.status === 'draft' ? (
          <button
            className="primary full"
            disabled={busy || submitted || !outfit.fullbody_url || loadedImage !== outfit.fullbody_url}
            onClick={() =>
              run(async () => {
                await api.invoke('companion.post_outfit_confirm', {
                  path: { outfit_id: outfit.id },
                  body: {}
                })
                await onUpdate()
              })
            }
            type="button"
          >
            满意，保存这套衣服
          </button>
        ) : null}
        {outfit.status === 'ready' ? (
          <button
            className="primary full"
            disabled={busy || submitted || outfit.active}
            onClick={() =>
              run(async () => {
                await api.invoke('companion.put_outfit_selection', { body: { outfit_id: outfit.id } })
                await onUpdate()
              })
            }
            type="button"
          >
            {outfit.active ? '正在穿着' : '穿上'}
          </button>
        ) : null}
      </div>
      {outfit.status === 'expired' ? <Notice>这套衣服的草稿已过期，请返回衣柜重新设计。</Notice> : null}
      {outfit.status === 'draft' ? (
        <details className="optional-details">
          <summary>调整画面</summary>
          <textarea
            aria-label="想调整哪些细节"
            disabled={busy || submitted}
            maxLength={500}
            onChange={event => setFeedback(event.target.value)}
            placeholder="例如：毛衣改成浅绿色，其他部分保持不变"
            rows={3}
            value={feedback}
          />
          <div className="button-row">
            <button disabled={busy || submitted || !feedback.trim()} onClick={() => regenerate('edit')} type="button">
              按想法修改
            </button>
            <button disabled={busy || submitted} onClick={() => regenerate('regenerate')} type="button">
              重新生成
            </button>
            <button disabled={busy || submitted} onClick={() => picker.current?.click()} type="button">
              使用自己的图片
            </button>
          </div>
        </details>
      ) : null}
      <div className="button-row">
        {outfit.description_status === 'failed' ? (
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api.invoke('companion.post_retry_outfit_description', {
                  path: { outfit_id: outfit.id }
                })
                await onUpdate()
              })
            }
            type="button"
          >
            重新准备衣服资料
          </button>
        ) : null}
      </div>
      <details className="optional-details">
        <summary>更多操作</summary>
        <div className="button-row">
          <button disabled={busy} onClick={() => run(onUpdate)} type="button">
            刷新结果
          </button>
          <button
            className="danger"
            disabled={busy || outfit.active}
            onClick={() =>
              run(async () => {
                if (
                  !window.confirm(
                    outfit.status === 'draft' ? '放弃这套衣服？制作草稿会被删除。' : '删除这套衣服？删除后无法恢复。'
                  )
                ) {
                  return
                }
                await api.invoke('companion.delete_outfit_route', { path: { outfit_id: outfit.id } })

                if (alive()) {
                  onClose()
                }

                await onUpdate()
              })
            }
            type="button"
          >
            {outfit.status === 'draft' ? '放弃草稿' : '删除'}
          </button>
        </div>
      </details>
      <ImageInput
        onFiles={([file]) => {
          if (file) {
            void run(async () => {
              const image = await readImage(file)

              if (alive()) {
                await api.invoke('companion.post_outfit_regenerate', {
                  path: { outfit_id: outfit.id },
                  body: { source: 'import', image: image.base64, content_type: image.type }
                })
                await onUpdate()
              }
            })
          }
        }}
        ref={picker}
      />
    </section>
  )
}
