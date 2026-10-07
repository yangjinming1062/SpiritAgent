import { IconPhoto, IconPlus, IconRefresh, IconX } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { RemoteApi } from './api'
import { readImage } from './api'
import type { Outfit, Scene, VideoPack } from './types'
import { Empty, Media, Notice, useAlive, useTask } from './ui'

interface SceneState {
  active: Scene | null
  pending: Scene | null
  regenerating: Scene | null
  policy: 'locked' | 'llm_may_replace'
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

function SceneLibrary({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [rows, setRows] = useState<Scene[]>([])
  const [state, setState] = useState<SceneState | null>(null)
  const [query, setQuery] = useState('')
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<Scene | null>(null)
  const [creating, setCreating] = useState(false)
  const [notes, setNotes] = useState('')
  const [image, setImage] = useState<Awaited<ReturnType<typeof readImage>> | null>(null)
  const [prompt, setPrompt] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const revision = useRef(0)

  const load = useCallback(
    async (offset = 0) => {
      const version = ++revision.current

      const [library, current] = await Promise.all([
        api.request<{ scenes: Scene[]; total: number }>(
          `/api/companion/scenes?q=${encodeURIComponent(query)}&offset=${offset}&limit=30`
        ),
        api.request<SceneState>('/api/companion/scenes/state')
      ])

      if (alive() && revision.current === version) {
        setRows(previous =>
          offset
            ? [...previous, ...library.scenes.filter(row => !previous.some(old => old.id === row.id))]
            : library.scenes
        )
        setTotal(library.total)
        setState(current)
        setSelected(previous => (previous ? (library.scenes.find(row => row.id === previous.id) ?? previous) : null))
      }
    },
    [alive, api, query]
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

  const refreshSelected = async () => {
    if (!selected) {
      return
    }

    const row = await api.request<Scene>(`/api/companion/scenes/${selected.id}`)

    if (alive()) {
      setSelected(previous => (previous?.id === row.id ? row : previous))
    }
  }

  return (
    <>
      <div className="library-toolbar">
        <input
          aria-label="搜索场景"
          onChange={event => {
            revision.current++
            setRows([])
            setQuery(event.target.value)
          }}
          placeholder="搜索场景"
          type="search"
          value={query}
        />
        <button
          aria-label="新建场景"
          className="icon-button"
          onClick={() => {
            setCreating(value => !value)
            setSelected(null)
          }}
          type="button"
        >
          <IconPlus />
        </button>
        <button
          aria-label="刷新场景"
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
        固定当前场景
      </label>
      {state?.pending || state?.regenerating ? (
        <section className="card">
          <strong>场景正在制作</strong>
          <p className="muted">{state.pending?.stage || state.regenerating?.regeneration?.stage}</p>
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
            取消制作
          </button>
        </section>
      ) : null}
      {creating ? (
        <section className="card create-card">
          <div className="card-title">
            <h3>新的场景</h3>
            <button aria-label="关闭创建" className="icon-button" onClick={() => setCreating(false)} type="button">
              <IconX size={19} />
            </button>
          </div>
          <textarea
            aria-label="场景描述"
            maxLength={500}
            onChange={event => setNotes(event.target.value)}
            placeholder="想去怎样的地方？写下风格、氛围或细节…"
            rows={4}
            value={notes}
          />
          {image ? <img alt="场景参考" className="reference-image" src={image.url} /> : null}
          <div className="button-row">
            <button disabled={busy || pending} onClick={() => picker.current?.click()} type="button">
              <IconPhoto size={17} />
              {image ? '更换图片' : '添加图片'}
            </button>
            {image ? (
              <button onClick={() => setImage(null)} type="button">
                移除图片
              </button>
            ) : null}
            <button
              className="primary"
              disabled={busy || pending}
              onClick={() =>
                run(async () => {
                  const row = await api.request<Scene>('/api/companion/scenes/generate', 'POST', {
                    notes: notes.trim() || undefined,
                    ...(image ? { image: image.base64, content_type: image.type } : {})
                  })

                  if (alive()) {
                    setSelected(row)
                    setCreating(false)
                  }

                  await load()
                })
              }
              type="button"
            >
              生成场景
            </button>
            {image ? (
              <button
                disabled={busy || pending}
                onClick={() =>
                  run(async () => {
                    const row = await api.request<Scene>('/api/companion/scenes/adopt', 'POST', {
                      image: image.base64,
                      content_type: image.type
                    })

                    if (alive()) {
                      setSelected(row)
                      setCreating(false)
                    }

                    await load()
                  })
                }
                type="button"
              >
                直接上传
              </button>
            ) : null}
            <button
              disabled={busy || pending}
              onClick={() =>
                run(async () => {
                  const row = await api.request<Scene>('/api/companion/scenes/prompt', 'POST', {
                    notes: notes.trim() || undefined
                  })

                  if (alive()) {
                    setPrompt(row.prompt)
                    setSelected(row)
                  }

                  await load()
                })
              }
              type="button"
            >
              准备制作说明
            </button>
          </div>
          {prompt ? (
            <details>
              <summary>制作说明</summary>
              <p className="prompt-copy">{prompt}</p>
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
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      {selected ? (
        <SceneDetail
          api={api}
          current={selected.id === state?.active?.id}
          key={selected.id}
          onClose={() => setSelected(null)}
          onUpdate={async (detail = true) => {
            await load()

            if (detail) {
              await refreshSelected()
            }
          }}
          scene={selected}
        />
      ) : null}
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
              <img alt={row.title || '场景'} loading="lazy" src={row.url} />
            ) : (
              <div className="asset-placeholder">✦</div>
            )}
            <div>
              <strong>{row.title || '新的场景'}</strong>
              <small>
                {state?.active?.id === row.id
                  ? '正在使用'
                  : row.status === 'ready'
                    ? '已就绪'
                    : row.status === 'pending'
                      ? '正在制作'
                      : row.status === 'description_failed'
                        ? '制作失败'
                        : row.status === 'cancelled'
                          ? '已取消'
                          : '待完善'}
              </small>
            </div>
          </button>
        ))}
      </div>
      {rows.length === 0 && !busy ? <Empty title="给伙伴一个新的地方">生成或上传一张喜欢的场景图片。</Empty> : null}
      {rows.length < total ? (
        <button className="load-more" disabled={busy} onClick={() => run(() => load(rows.length))} type="button">
          更多场景
        </button>
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
  onUpdate: (detail?: boolean) => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [title, setTitle] = useState(scene.title)
  const [description, setDescription] = useState(scene.description)
  const picker = useRef<HTMLInputElement>(null)

  return (
    <section className="card asset-detail">
      <div className="card-title">
        <h3>{scene.title || '场景详情'}</h3>
        <button aria-label="关闭详情" className="icon-button" onClick={onClose} type="button">
          <IconX size={19} />
        </button>
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
      {scene.error || scene.regeneration?.error ? <Notice>{scene.error || scene.regeneration?.error}</Notice> : null}
      {error ? <Notice>{error}</Notice> : null}
      <div className="button-row">
        <button
          className="primary"
          disabled={current || busy || scene.status !== 'ready'}
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
          {current ? '正在使用' : '使用场景'}
        </button>
        <button
          disabled={busy || !title.trim() || !description.trim()}
          onClick={() =>
            run(async () => {
              await api.request(`/api/companion/scenes/${scene.id}`, 'PATCH', {
                title,
                description
              })
              await onUpdate()
            })
          }
          type="button"
        >
          保存描述
        </button>
        <button
          disabled={busy || scene.status === 'pending' || scene.regeneration?.status === 'pending'}
          onClick={() =>
            run(async () => {
              await api.request(`/api/companion/scenes/${scene.id}/regenerate`, 'POST', {})
              await onUpdate()
            })
          }
          type="button"
        >
          重新生成
        </button>
        <button disabled={busy} onClick={() => picker.current?.click()} type="button">
          上传替换
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
            重试分析
          </button>
        ) : null}
        <button
          className="danger"
          disabled={busy || current}
          onClick={() =>
            run(async () => {
              await api.request(`/api/companion/scenes/${scene.id}`, 'DELETE')

              if (alive()) {
                onClose()
              }

              await onUpdate(false)
            })
          }
          type="button"
        >
          删除
        </button>
      </div>
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

function Wardrobe({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [outfits, setOutfits] = useState<Outfit[]>([])
  const [packs, setPacks] = useState<VideoPack[]>([])
  const [policy, setPolicy] = useState('llm_may_replace')
  const [creating, setCreating] = useState(false)
  const [description, setDescription] = useState('')
  const [image, setImage] = useState<Awaited<ReturnType<typeof readImage>> | null>(null)
  const [prompt, setPrompt] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const picker = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    const [wardrobe, videos] = await Promise.all([
      api.request<{ outfits: Outfit[]; policy: string }>('/api/companion/outfits'),
      api.request<{ packs: VideoPack[] }>('/api/companion/video-packs')
    ])

    if (alive()) {
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
      <div className="library-toolbar">
        <h3>伙伴的衣柜</h3>
        <button
          aria-label="制作新外观"
          className="icon-button"
          onClick={() => {
            setCreating(value => !value)
            setSelectedId(null)
          }}
          type="button"
        >
          <IconPlus />
        </button>
        <button aria-label="刷新衣柜" className="icon-button" disabled={busy} onClick={() => run(load)} type="button">
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
        固定当前外观
      </label>
      {creating ? (
        <section className="card">
          <h3>设计新外观</h3>
          <textarea
            aria-label="外观描述"
            maxLength={500}
            onChange={event => setDescription(event.target.value)}
            placeholder="写下想要的衣服、颜色、风格…"
            rows={4}
            value={description}
          />
          {image ? <img alt="外观参考" className="reference-image" src={image.url} /> : null}
          <div className="button-row">
            <button disabled={busy} onClick={() => picker.current?.click()} type="button">
              <IconPhoto size={17} />
              {image ? '更换图片' : '添加图片'}
            </button>
            {image ? (
              <button onClick={() => setImage(null)} type="button">
                移除图片
              </button>
            ) : null}
            <button
              className="primary"
              disabled={busy || (!description.trim() && !image)}
              onClick={() =>
                run(async () => {
                  const outfit = await api.request<Outfit>('/api/companion/outfits', 'POST', {
                    description: description.trim() || undefined,
                    ...(image ? { image: image.base64, content_type: image.type } : {})
                  })

                  if (alive()) {
                    setSelectedId(outfit.id)
                    setCreating(false)
                  }

                  await load()
                })
              }
              type="button"
            >
              生成外观
            </button>
            {image ? (
              <button
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    const outfit = await api.request<Outfit>('/api/companion/outfits/adopt', 'POST', {
                      image: image.base64,
                      content_type: image.type,
                      description: description.trim() || undefined
                    })

                    if (alive()) {
                      setSelectedId(outfit.id)
                      setCreating(false)
                    }

                    await load()
                  })
                }
                type="button"
              >
                直接上传
              </button>
            ) : null}
            <button
              disabled={busy || (!description.trim() && !image)}
              onClick={() =>
                run(async () => {
                  const result = await api.request<{ prompt: string }>('/api/companion/outfits/prompt', 'POST', {
                    description: description.trim() || undefined,
                    ...(image ? { image: image.base64, content_type: image.type } : {})
                  })

                  if (alive()) {
                    setPrompt(result.prompt)
                  }
                })
              }
              type="button"
            >
              准备制作说明
            </button>
          </div>
          {busy ? <p className="muted">正在处理，暂时离开不会重复提交制作。</p> : null}
          {prompt ? (
            <details>
              <summary>制作说明</summary>
              <p className="prompt-copy">{prompt}</p>
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
                  const value = await readImage(file)

                  if (alive()) {
                    setImage(value)
                  }
                })
              }
            }}
            ref={picker}
            type="file"
          />
        </section>
      ) : null}
      {error ? (
        <Notice>
          {error}
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
      {outfits.length === 0 && !busy ? <Empty title="留下一套喜欢的外观">上传图片或描述心中的新造型。</Empty> : null}
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
  const [feedback, setFeedback] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const pack = packs.find(item => item.status === 'ready') ?? packs[0]

  return (
    <section className="card asset-detail">
      <div className="card-title">
        <h3>{outfit.name || '外观详情'}</h3>
        <button aria-label="关闭详情" className="icon-button" onClick={onClose} type="button">
          <IconX size={19} />
        </button>
      </div>
      {outfit.fullbody_url ? (
        <Media
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
      {outfit.status === 'draft' || outfit.status === 'expired' ? (
        <textarea
          aria-label="外观修改建议"
          maxLength={500}
          onChange={event => setFeedback(event.target.value)}
          placeholder="写下需要调整的细节…"
          rows={3}
          value={feedback}
        />
      ) : null}
      {error ? (
        <Notice>
          {error}
          <span>请先刷新查看原来的制作结果，再决定是否重新制作。</span>
        </Notice>
      ) : null}
      <div className="button-row">
        {outfit.status === 'draft' ? (
          <>
            <button
              className="primary"
              disabled={busy || !outfit.fullbody_url}
              onClick={() =>
                run(async () => {
                  await api.request(`/api/companion/outfits/${outfit.id}/confirm`, 'POST', {})
                  await onUpdate()
                })
              }
              type="button"
            >
              确认入柜
            </button>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.request(`/api/companion/outfits/${outfit.id}/regenerate`, 'POST', {
                    feedback: feedback.trim() || undefined,
                    mode: 'edit'
                  })
                  await onUpdate()
                })
              }
              type="button"
            >
              微调
            </button>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.request(`/api/companion/outfits/${outfit.id}/regenerate`, 'POST', {
                    feedback: feedback.trim() || undefined,
                    mode: 'regenerate'
                  })
                  await onUpdate()
                })
              }
              type="button"
            >
              重新生成
            </button>
            <button disabled={busy} onClick={() => picker.current?.click()} type="button">
              上传替换
            </button>
          </>
        ) : null}
        {outfit.status === 'ready' && pack ? (
          <button
            className="primary"
            disabled={busy || pack.status !== 'ready' || outfit.active}
            onClick={() =>
              run(async () => {
                await api.request(`/api/companion/video-packs/${pack.id}/activate`, 'PUT', {})
                await onUpdate()
              })
            }
            type="button"
          >
            {outfit.active ? '正在穿着' : pack.status === 'ready' ? '穿上这套' : '动作正在准备'}
          </button>
        ) : null}
        {pack?.can_retry ? (
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api.request(`/api/companion/video-packs/${pack.id}/retry`, 'POST', {})
                await onUpdate()
              })
            }
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
            重试描述
          </button>
        ) : null}
        <button disabled={busy} onClick={() => run(onUpdate)} type="button">
          刷新结果
        </button>
        <button
          className="danger"
          disabled={busy || outfit.active}
          onClick={() =>
            run(async () => {
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
