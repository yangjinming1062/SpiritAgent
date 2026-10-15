import { IconPhoto, IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { RemoteApi } from './api'
import { ApiError, readImage } from './api'
import type { Outfit, Scene, VideoPack } from './types'
import { Empty, Media, Notice, PageHeader, useAlive, usePageState, useTask } from './ui'

interface SceneState {
  active: Scene | null
  pending: Scene | null
  regenerating: Scene | null
  policy: 'locked' | 'llm_may_replace'
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

export function Assets({
  api,
  kind,
  onBack,
  refresh
}: {
  api: RemoteApi
  kind: 'scene' | 'outfit'
  onBack: () => void
  refresh: number
}): ReactElement {
  return kind === 'scene' ? (
    <SceneLibrary api={api} onBack={onBack} refresh={refresh} />
  ) : (
    <Wardrobe api={api} onBack={onBack} refresh={refresh} />
  )
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
  const [submitted, setSubmitted] = usePageState(`${kind}.submitted`, false)
  const [reconciled, setReconciled] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const scene = kind === 'scene'
  const label = scene ? '背景' : '衣服'
  const imageBody = image ? { image: image.base64, content_type: image.type } : {}

  return (
    <section className="creation-screen">
      <button className="back-link" onClick={onClose} type="button">
        ‹ 返回{scene ? '背景' : '衣柜'}
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
              <button
                onClick={() =>
                  run(async () => {
                    await onCheck()
                    if (alive()) {
                      setReconciled(true)
                    }
                  })
                }
                type="button"
              >
                刷新制作列表
              </button>
              <button disabled={!reconciled} onClick={() => setSubmitted(false)} type="button">
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
              setSubmitted(true)
              setReconciled(false)
              const path =
                source === 'ai'
                  ? scene
                    ? '/api/companion/scenes/generate'
                    : '/api/companion/outfits'
                  : `/api/companion/${scene ? 'scenes' : 'outfits'}/adopt`
              const body =
                source === 'upload' && scene
                  ? imageBody
                  : { [scene ? 'notes' : 'description']: description.trim() || undefined, ...imageBody }
              let result: { id: number }
              try {
                result = await api.request<{ id: number }>(path, 'POST', body)
              } catch (failure) {
                if (alive() && failure instanceof ApiError && failure.status < 500) {
                  setSubmitted(false)
                }
                throw failure
              }
              if (alive()) {
                setDescription('')
                setImage(null)
                setPrompt('')
                setSource('ai')
                await onCreated(result.id)
                setSubmitted(false)
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
                  setSubmitted(true)
                  setReconciled(false)
                  let result: Scene
                  try {
                    result = await api.request<Scene>('/api/companion/scenes/prompt', 'POST', {
                      notes: description.trim() || undefined
                    })
                  } catch (failure) {
                    if (alive() && failure instanceof ApiError && failure.status < 500) {
                      setSubmitted(false)
                    }
                    throw failure
                  }
                  if (alive()) {
                    setPrompt(result.prompt)
                    setSource('ai')
                    await onCreated(result.id)
                    setSubmitted(false)
                  }
                } else {
                  const result = await api.request<{ prompt: string }>('/api/companion/outfits/prompt', 'POST', {
                    description: description.trim() || undefined,
                    ...imageBody
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
      <input
        accept="image/png,image/jpeg,image/webp,image/gif"
        hidden
        onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''
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
        type="file"
      />
    </section>
  )
}

function SceneLibrary({ api, onBack, refresh }: { api: RemoteApi; onBack: () => void; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [rows, setRows] = useState<Scene[]>([])
  const [state, setState] = useState<SceneState | null>(null)
  const [query, setQuery] = usePageState('scene.query', '')
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = usePageState<Scene | null>('scene.selected', null)
  const [creating, setCreating] = usePageState('scene.creating', false)
  const revision = useRef(0)
  const selectedId = selected?.id

  useEffect(() => {
    document.querySelector('.content')?.scrollTo({ top: 0 })
  }, [creating, selectedId])

  const load = useCallback(
    async (offset = 0) => {
      const version = ++revision.current

      const [library, current, detail] = await Promise.all([
        api.request<{ scenes: Scene[]; total: number }>(
          `/api/companion/scenes?q=${encodeURIComponent(query)}&offset=${offset}&limit=30`
        ),
        api.request<SceneState>('/api/companion/scenes/state'),
        selectedId
          ? api.request<Scene>(`/api/companion/scenes/${selectedId}`).catch(failure => {
              if (failure instanceof ApiError && failure.status === 404) {
                return null
              }
              throw failure
            })
          : null
      ])

      if (alive() && revision.current === version) {
        setRows(previous =>
          offset
            ? [...previous, ...library.scenes.filter(row => !previous.some(old => old.id === row.id))]
            : library.scenes
        )
        setTotal(library.total)
        setState(current)
        setSelected(previous => (previous?.id === selectedId ? detail : previous))
      }
    },
    [alive, api, query, selectedId, setSelected]
  )

  useEffect(() => {
    void runLatest(() => load())
  }, [load, refresh, runLatest])
  const pending = Boolean(state?.pending || state?.regenerating)

  useEffect(() => {
    if (!pending) {
      return
    }

    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void runLatest(() => load())
      }
    }, 5000)

    return () => clearInterval(timer)
  }, [pending, load, runLatest])

  return (
    <>
      {!creating && !selected ? (
        <>
          <button className="back-link" onClick={onBack} type="button">
            ‹ 伙伴
          </button>
          <PageHeader subtitle="描述一个喜欢的地方，让 AI 画出来" title="窗口背景" />
          <div className="library-toolbar">
            <input
              aria-label="搜索背景"
              onChange={event => {
                revision.current++
                setRows([])
                setQuery(event.target.value)
              }}
              placeholder="搜索背景"
              type="search"
              value={query}
            />
            <button
              aria-label="创建背景"
              className="primary"
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
              onClick={() => run(() => load())}
              type="button"
            >
              <IconRefresh size={20} />
            </button>
          </div>
          <label className="policy-toggle">
            <input
              checked={state?.policy === 'locked'}
              disabled={!state || busy}
              onChange={event => {
                const policy = event.target.checked ? 'locked' : 'llm_may_replace'
                void run(async () => {
                  await api.request('/api/companion/scenes/policy', 'PATCH', {
                    policy
                  })
                  await load()
                })
              }}
              type="checkbox"
            />
            保持这个背景，不自动更换
          </label>
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
                  await api.request(`/api/companion/scenes/${id}/discard`, 'POST', {})
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
          onCheck={() => load()}
          onClose={() => setCreating(false)}
          onCreated={async id => {
            const row = await api.request<Scene>(`/api/companion/scenes/${id}`)
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
          onClose={() => setSelected(null)}
          onUpdate={() => load()}
          scene={selected}
        />
      ) : null}
      {!creating && !selected ? (
        <>
          <div className="asset-grid">
            {rows.map(row => (
              <button
                className={`asset-card ${state?.active?.id === row.id ? 'active' : ''}`}
                key={row.id}
                onClick={() => {
                  setSelected(row)
                  setCreating(false)
                }}
                type="button"
              >
                {row.url ? (
                  <img alt={row.title || '背景'} loading="lazy" src={row.url} />
                ) : (
                  <div className="asset-placeholder">✦</div>
                )}
                <div>
                  <strong>{row.title || '新的背景'}</strong>
                  <small>
                    {state?.active?.id === row.id
                      ? '正在使用'
                      : row.status === 'ready'
                        ? '已就绪'
                        : row.status === 'pending'
                          ? '正在制作'
                          : row.status === 'description_failed' || row.status === 'failed'
                            ? '制作失败'
                            : row.status === 'cancelled'
                              ? '已取消'
                              : '待完善'}
                  </small>
                </div>
              </button>
            ))}
          </div>
          {rows.length === 0 && !busy ? (
            <Empty title="给伙伴一个新的地方">描述想去的地方，AI 会为你制作背景。</Empty>
          ) : null}
          {rows.length < total ? (
            <button className="load-more" disabled={busy} onClick={() => run(() => load(rows.length))} type="button">
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
  const [submitted, setSubmitted] = usePageState(`scene.${scene.id}.submitted`, false)
  const [reconciled, setReconciled] = useState(false)
  const previous = useRef({ title: scene.title, description: scene.description })
  const [loadedImage, setLoadedImage] = useState('')
  const picker = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const old = previous.current
    setTitle(value => (value === old.title ? scene.title : value))
    setDescription(value => (value === old.description ? scene.description : value))
    previous.current = { title: scene.title, description: scene.description }
  }, [scene.title, scene.description, setTitle, setDescription])

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ‹ 返回背景
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
              await api.request(`/api/companion/scenes/${scene.id}`, 'PATCH', { title, description })
              await onUpdate()
            })
          }
          type="button"
        >
          保存修改
        </button>
      </details>
      {scene.error || scene.regeneration?.error ? <Notice>{scene.error || scene.regeneration?.error}</Notice> : null}
      {error ? <Notice>{error}</Notice> : null}
      {submitted && !busy ? (
        <div className="creation-status">
          <p>上次制作的结果尚未确认。请先刷新，核对原来的结果。</p>
          <div className="button-row">
            <button
              onClick={() =>
                run(async () => {
                  await onUpdate()
                  if (alive()) {
                    setReconciled(true)
                  }
                })
              }
              type="button"
            >
              刷新结果
            </button>
            <button disabled={!reconciled} onClick={() => setSubmitted(false)} type="button">
              已核对，继续调整
            </button>
          </div>
        </div>
      ) : null}
      {scene.url || scene.status === 'pending' ? (
        <div className="creation-footer">
          <button
            className="primary full"
            disabled={current || busy || scene.status !== 'ready' || loadedImage !== scene.url}
            onClick={() =>
              run(async () => {
                await api.request('/api/companion/scenes/activate', 'POST', {
                  scene_id: scene.id
                })
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
            disabled={busy || submitted || scene.status === 'pending' || scene.regeneration?.status === 'pending'}
            onClick={() =>
              run(async () => {
                setSubmitted(true)
                setReconciled(false)
                try {
                  await api.request(`/api/companion/scenes/${scene.id}/regenerate`, 'POST', {})
                  await onUpdate()
                  setSubmitted(false)
                } catch (failure) {
                  if (alive() && failure instanceof ApiError && failure.status < 500) {
                    setSubmitted(false)
                  }
                  throw failure
                }
              })
            }
            type="button"
          >
            重新制作画面
          </button>
          <button disabled={busy} onClick={() => picker.current?.click()} type="button">
            使用自己的图片
          </button>
          {scene.status === 'description_failed' ? (
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.request(`/api/companion/scenes/${scene.id}/analyze`, 'POST', {})
                  await onUpdate()
                })
              }
              type="button"
            >
              重新准备图片
            </button>
          ) : null}
          <button
            className="danger"
            disabled={busy || current}
            onClick={() =>
              run(async () => {
                if (!window.confirm('删除这个背景？删除后无法恢复。')) {
                  return
                }
                await api.request(`/api/companion/scenes/${scene.id}`, 'DELETE')

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
      <input
        accept="image/png,image/jpeg,image/webp,image/gif"
        hidden
        onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''

          if (file) {
            void run(async () => {
              const image = await readImage(file)

              if (alive()) {
                await api.request(`/api/companion/scenes/${scene.id}/adopt`, 'POST', {
                  image: image.base64,
                  content_type: image.type
                })
                await onUpdate()
              }
            })
          }
        }}
        ref={picker}
        type="file"
      />
    </section>
  )
}

function Wardrobe({ api, onBack, refresh }: { api: RemoteApi; onBack: () => void; refresh: number }): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [outfits, setOutfits] = useState<Outfit[]>([])
  const [packs, setPacks] = useState<VideoPack[]>([])
  const [policy, setPolicy] = useState('llm_may_replace')
  const [creating, setCreating] = usePageState('outfit.creating', false)
  const [selectedId, setSelectedId] = usePageState<number | null>('outfit.selected', null)
  const revision = useRef(0)

  useEffect(() => {
    document.querySelector('.content')?.scrollTo({ top: 0 })
  }, [creating, selectedId])

  const load = useCallback(async () => {
    const current = ++revision.current
    const [wardrobe, videos] = await Promise.all([
      api.request<{ outfits: Outfit[]; policy: string }>('/api/companion/outfits'),
      api.request<{ packs: VideoPack[] }>('/api/companion/video-packs')
    ])

    if (alive() && current === revision.current) {
      setOutfits(wardrobe.outfits)
      setPolicy(wardrobe.policy)
      setPacks(videos.packs)
    }
  }, [api, alive])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  const pending =
    outfits.some(outfit => outfit.description_status === 'pending' || outfit.description_status === 'processing') ||
    packs.some(pack => pack.status === 'processing')

  useEffect(() => {
    if (!pending) {
      return
    }

    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void runLatest(load)
      }
    }, 5000)

    return () => clearInterval(timer)
  }, [load, pending, runLatest])

  const selected = outfits.find(outfit => outfit.id === selectedId)

  return (
    <>
      {!creating && !selected ? (
        <>
          <button className="back-link" onClick={onBack} type="button">
            ‹ 伙伴
          </button>
          <PageHeader subtitle="描述喜欢的颜色和风格，AI 帮你制作" title="衣柜" />
          <div className="library-toolbar">
            <h3>伙伴的衣柜</h3>
            <button
              aria-label="制作新外观"
              className="primary"
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
          <label className="policy-toggle">
            <input
              checked={policy === 'locked'}
              disabled={busy}
              onChange={event => {
                const value = event.target.checked ? 'locked' : 'llm_may_replace'
                void run(async () => {
                  await api.request('/api/companion/outfits/policy', 'PATCH', {
                    policy: value
                  })
                  await load()
                })
              }}
              type="checkbox"
            />
            保持这套衣服，不自动更换
          </label>
        </>
      ) : null}
      {creating ? (
        <AssetCreator
          api={api}
          kind="outfit"
          onCheck={load}
          onClose={() => setCreating(false)}
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
          onClose={() => setSelectedId(null)}
          onUpdate={load}
          outfit={selected}
          packs={packs.filter(pack => pack.outfit_id === selected.id)}
        />
      ) : null}
      {!creating && !selected ? (
        <>
          <div className="asset-grid outfits">
            {outfits.map(outfit => (
              <button
                className={`asset-card ${outfit.active ? 'active' : ''}`}
                key={outfit.id}
                onClick={() => {
                  setSelectedId(outfit.id)
                  setCreating(false)
                }}
                type="button"
              >
                {outfit.fullbody_url ? (
                  <img alt={outfit.name} loading="lazy" src={outfit.fullbody_url} />
                ) : (
                  <div className="asset-placeholder">✦</div>
                )}
                <div>
                  <strong>{outfit.name || '新外观'}</strong>
                  <small>
                    {outfit.active
                      ? '正在穿着'
                      : outfit.status === 'ready'
                        ? '已入柜'
                        : outfit.status === 'draft'
                          ? '待确认'
                          : '已过期'}
                  </small>
                </div>
              </button>
            ))}
          </div>
          {outfits.length === 0 && !busy ? (
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
  packs,
  onUpdate,
  onClose
}: {
  api: RemoteApi
  outfit: Outfit
  packs: VideoPack[]
  onUpdate: () => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [feedback, setFeedback] = usePageState(`outfit.${outfit.id}.feedback`, '')
  const [submitted, setSubmitted] = usePageState(`outfit.${outfit.id}.submitted`, false)
  const [reconciled, setReconciled] = useState(false)
  const [loadedImage, setLoadedImage] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const pack = packs.find(item => item.status === 'ready') ?? packs[0]
  const produce = (path: string, body: Record<string, unknown>) =>
    run(async () => {
      setSubmitted(true)
      setReconciled(false)
      try {
        await api.request(path, 'POST', body)
        await onUpdate()
        setSubmitted(false)
        setFeedback('')
      } catch (failure) {
        if (alive() && failure instanceof ApiError && failure.status < 500) {
          setSubmitted(false)
        }
        throw failure
      }
    })
  const regenerate = (mode: 'edit' | 'regenerate') =>
    produce(`/api/companion/outfits/${outfit.id}/regenerate`, {
      feedback: feedback.trim() || undefined,
      mode
    })

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ‹ 返回衣柜
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
      {outfit.initial_video_error || outfit.description_error || pack?.error ? (
        <Notice>{outfit.initial_video_error || outfit.description_error || pack?.error}</Notice>
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      {submitted && !busy ? (
        <div className="creation-status">
          <p>上次制作的结果尚未确认。请先刷新，核对原来的结果。</p>
          <div className="button-row">
            <button
              onClick={() =>
                run(async () => {
                  await onUpdate()
                  if (alive()) {
                    setReconciled(true)
                  }
                })
              }
              type="button"
            >
              刷新结果
            </button>
            <button disabled={!reconciled} onClick={() => setSubmitted(false)} type="button">
              已核对，继续调整
            </button>
          </div>
        </div>
      ) : null}
      <div className="creation-footer">
        {outfit.status === 'draft' ? (
          <>
            <button
              className="primary full"
              disabled={busy || submitted || !outfit.fullbody_url || loadedImage !== outfit.fullbody_url}
              onClick={() =>
                run(async () => {
                  await api.request(`/api/companion/outfits/${outfit.id}/confirm`, 'POST', {})
                  await onUpdate()
                })
              }
              type="button"
            >
              满意，保存这套衣服
            </button>
          </>
        ) : null}
        {outfit.status === 'ready' ? (
          <button
            className="primary full"
            disabled={busy || submitted || (Boolean(pack) && pack?.status !== 'ready') || outfit.active}
            onClick={() =>
              pack
                ? run(async () => {
                    await api.request(`/api/companion/video-packs/${pack.id}/activate`, 'PUT', {})
                    await onUpdate()
                  })
                : produce('/api/companion/video-packs/generate', { outfit_id: outfit.id })
            }
            type="button"
          >
            {outfit.active
              ? '正在穿着'
              : pack?.status === 'ready'
                ? '穿上'
                : pack?.status === 'processing'
                  ? '正在准备穿着动作…'
                  : pack
                    ? '穿着动作尚未准备好'
                    : '准备穿上这套衣服'}
          </button>
        ) : null}
      </div>
      {pack?.status === 'processing' ? (
        <p aria-live="polite" className="creation-status">
          衣服已经保存，正在准备穿上后的动作。可以离开这里，稍后回来继续。
        </p>
      ) : null}
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
        {pack?.can_retry ? (
          <button
            disabled={busy || submitted}
            onClick={() => produce(`/api/companion/video-packs/${pack.id}/retry`, {})}
            type="button"
          >
            重试动作制作
          </button>
        ) : null}
        {outfit.description_status === 'failed' ? (
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api.request(`/api/companion/outfits/${outfit.id}/description/retry`, 'POST', {})
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
                await api.request(`/api/companion/outfits/${outfit.id}`, 'DELETE')

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
      <input
        accept="image/png,image/jpeg,image/webp,image/gif"
        hidden
        onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''

          if (file) {
            void run(async () => {
              const image = await readImage(file)

              if (alive()) {
                await api.request(`/api/companion/outfits/${outfit.id}/adopt`, 'POST', {
                  image: image.base64,
                  content_type: image.type
                })
                await onUpdate()
              }
            })
          }
        }}
        ref={picker}
        type="file"
      />
    </section>
  )
}
