import { IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiInput } from '../shared/api'
import type { ApiSchemas } from '../shared/api.generated'

import { type RemoteApi } from './api'
import { AssetResultRecovery, useAssetSubmission } from './asset-submission'
import { AssetCreator, AssetPolicyToggle } from './AssetCreator'
import { readImage } from './browser'
import { MediaReviewButton } from './MediaReviewButton'
import { usePageScroll, usePageState } from './page-state'
import { requireCurrentRead, useAlive, useTask, useVisiblePolling } from './tasks'
import type { Outfit } from './types'
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

export function Wardrobe({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [outfits, setOutfits] = useState<Outfit[]>([])
  const [reviewJobs, setReviewJobs] = useState<ApiSchemas['MediaJobView'][]>([])
  const [policy, setPolicy] = useState<string | null>(null)
  const [policySaving, setPolicySaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  // 详情与创建视图随组件生命周期，切 tab 或分区即回一级列表。
  const [creating, setCreating] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const revision = useRef(0)

  const load = useCallback(async () => {
    const current = ++revision.current
    const [wardrobe, pendingJobs] = await Promise.all([
      api.invoke('companion.get_outfits'),
      api.invoke('media.get_outfit_review_jobs')
    ])

    if (alive()) {
      requireCurrentRead(current, revision.current)
      setOutfits(wardrobe.outfits)
      setReviewJobs(pendingJobs)
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
          {reviewJobs.map(job => (
            <section className="card" key={job.id}>
              <p>{job.slots[0]?.error}</p>
              <MediaReviewButton api={api} jobId={job.id} onChanged={load} />
            </section>
          ))}
          <AssetPolicyToggle
            checked={policy === 'llm_may_replace'}
            description="伙伴可以自行设计并收藏新的衣服素材。"
            disabled={busy || !loaded}
            loaded={loaded}
            onChange={checked => {
              const value = checked ? 'llm_may_replace' : 'locked'
              void run(async () => {
                setPolicySaving(true)
                try {
                  const next = await api.invoke('companion.patch_outfit_policy', { body: { policy: value } })
                  if (alive()) {
                    setPolicy(next.policy)
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
      {creating ? (
        <AssetCreator
          api={api}
          kind="outfit"
          onCheck={load}
          onClose={() => {
            setCreating(false)
            focusAfterRender('[data-create-asset="outfit"]')
          }}
          onCreated={({ kind, value }) => {
            if (kind === 'outfit') {
              revision.current++
              setOutfits(previous =>
                previous.some(outfit => outfit.id === value.id) ? previous : [...previous, value]
              )
              setSelectedId(value.id)
              setCreating(false)
            }
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
            focusAfterRender(`[data-card-key="outfit-${selected.id}"]`)
          }}
          onRegenerated={updated => {
            if (alive()) {
              revision.current++
              setOutfits(previous => previous.map(outfit => (outfit.id === updated.id ? updated : outfit)))
            }
          }}
          onUpdate={load}
          outfit={selected}
        />
      ) : null}
      {!creating && !selected ? (
        <>
          {!loaded && !outfits.length ? <GridSkeleton /> : null}
          <div className="asset-grid outfits">
            {outfits.map(outfit => {
              return (
                <LibraryCard
                  cardKey={`outfit-${outfit.id}`}
                  imageAlt={outfit.name}
                  imageUrl={outfit.fullbody_url}
                  key={outfit.id}
                  meta={outfit.status === 'ready' ? '已入柜' : outfit.status === 'draft' ? '待确认' : '已过期'}
                  onSelect={() => {
                    setSelectedId(outfit.id)
                    setCreating(false)
                  }}
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
  onRegenerated,
  onUpdate,
  onClose
}: {
  api: RemoteApi
  outfit: Outfit
  onRegenerated: (outfit: Outfit) => void
  onUpdate: () => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [feedback, setFeedback] = usePageState(`outfit.${outfit.id}.feedback`, '')
  const { check, clearSubmission, reconciled, submit, submitted } = useAssetSubmission(`outfit.${outfit.id}`, onUpdate)
  const [loadedImage, setLoadedImage] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const regenerate = (body: ApiInput<'companion.post_outfit_regenerate'>['body']) =>
    submit(
      () => api.invoke('companion.post_outfit_regenerate', { path: { outfit_id: outfit.id }, body }),
      updated => {
        onRegenerated(updated)
        if (alive() && body.source === 'generate') {
          setFeedback('')
        }
      }
    )
  const generate = (mode: 'edit' | 'regenerate') =>
    run(() => regenerate({ source: 'generate', feedback: feedback.trim() || undefined, mode }))

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      <AssetSteps step={outfit.status === 'ready' ? 3 : 2} />
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
      {outfit.status === 'draft' ? (
        <div className="creation-footer">
          <button
            className="primary full"
            disabled={busy || submitted || !outfit.fullbody_url || loadedImage !== outfit.fullbody_url}
            onClick={() =>
              run(async () => {
                await api.invoke('companion.post_outfit_confirm', {
                  path: { outfit_id: outfit.id }
                })
                await onUpdate()
              })
            }
            type="button"
          >
            满意，保存这套衣服
          </button>
        </div>
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
            <button disabled={busy || submitted || !feedback.trim()} onClick={() => generate('edit')} type="button">
              按想法修改
            </button>
            <button disabled={busy || submitted} onClick={() => generate('regenerate')} type="button">
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
            disabled={busy}
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
      <FileInput
        onFiles={([file]) => {
          if (file) {
            void run(async () => {
              const image = await readImage(file)

              if (alive()) {
                await regenerate({ source: 'analysis', image: image.base64, content_type: image.type })
              }
            })
          }
        }}
        ref={picker}
      />
    </section>
  )
}
