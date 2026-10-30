import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import {
  emptyVisualCreation,
  loadVisualMaterials,
  type VisualCreationDraft,
  visualCreationReady,
  type VisualMaterialOptions
} from '../shared/visuals'

import type { RemoteApi } from './api'
import { errorText } from './api'
import { Notice, useAlive, usePageState, useTask } from './ui'

export function VisualCreate({
  api,
  onDone
}: {
  api: RemoteApi
  onDone: (visualId?: string) => Promise<void>
}): ReactElement {
  const alive = useAlive()
  const [draft, setDraft] = usePageState('visuals.creation', emptyVisualCreation)
  const [requests, setRequests] = usePageState<Record<string, string>>('visuals.creationRequests', {})
  const [file, setFile] = usePageState<File | null>('visuals.uploadFile', null, false)
  const [preview, setPreview] = useState('')
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const [materialError, setMaterialError] = useState('')
  const { run, busy, error } = useTask()
  const upload = draft.mode === 'upload'
  const patch = (value: Partial<VisualCreationDraft>): void => setDraft(previous => ({ ...previous, ...value }))

  useEffect(() => {
    if (draft.mode !== 'create') {
      return
    }
    let current = true
    void loadVisualMaterials({
      outfits: () => api.invoke('companion.get_outfits'),
      scenes: cursor => api.invoke('companion_scenes.scene_list_route', { query: { cursor, limit: 100 } }),
      actions: cursor =>
        api.invoke('companion_actions.list_actions', { query: { cursor, limit: 100, group: 'in_library' } })
    })
      .then(value => {
        if (current && alive()) {
          setMaterials(value)
          setMaterialError('')
        }
      })
      .catch(failure => {
        if (current && alive()) {
          setMaterialError(errorText(failure))
        }
      })
    return () => {
      current = false
    }
  }, [api, draft.mode, alive])

  useEffect(() => {
    if (!file) {
      return
    }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => {
      URL.revokeObjectURL(url)
      setPreview('')
    }
  }, [file])

  const submit = async (): Promise<void> => {
    if (upload && file) {
      const bytes = new Uint8Array(await file.arrayBuffer())
      if (!alive()) {
        return
      }
      const result = await api.invoke('companion_visuals.upload_visual', {
        body: bytes,
        query: {
          filename: file.name,
          title: draft.title,
          description: draft.description,
          asset_kind: draft.assetKind
        }
      })
      if (alive()) {
        setFile(null)
        patch({ mode: null })
        await onDone(result.id)
      }
      return
    }
    const key = JSON.stringify(draft)
    const request_id = requests[key] ?? crypto.randomUUID()
    setRequests(previous => ({ ...previous, [key]: request_id }))
    const requirements = draft.requirements.trim()
    if (draft.assetKind === 'actor') {
      await api.invoke('companion_visuals.create_actor_clip', {
        body: { request_id, outfit_id: draft.outfitId, action_id: draft.actionId, requirements }
      })
    } else if (draft.assetKind === 'scene') {
      await api.invoke('companion_visuals.create_scene_media', {
        body: { request_id, scene_id: draft.sceneId, requirements }
      })
    } else {
      await api.invoke('companion_visuals.create_cinematic', {
        body: {
          request_id,
          outfit_id: draft.outfitId,
          scene_id: draft.sceneId,
          action_id: draft.actionId,
          media_kind: draft.mediaKind,
          requirements
        }
      })
    }
    if (alive()) {
      setRequests(previous => {
        const pending = { ...previous }
        delete pending[key]
        return pending
      })
      patch({ mode: null })
      await onDone()
    }
  }

  const selectMaterial = (
    label: string,
    key: 'outfitId' | 'sceneId' | 'actionId',
    options: VisualMaterialOptions['outfits']
  ): ReactElement => (
    <label>
      {label}
      <select disabled={busy} onChange={event => patch({ [key]: event.target.value })} value={draft[key]}>
        <option value="">请选择</option>
        {options.map(option => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <>
      <div className="button-row">
        <button disabled={busy} onClick={() => patch({ mode: 'create' })} type="button">
          创建影像
        </button>
        <button disabled={busy} onClick={() => patch({ mode: 'upload' })} type="button">
          上传影像
        </button>
      </div>
      {draft.mode && (
        <form
          className="card"
          onSubmit={event => {
            event.preventDefault()
            void run(submit)
          }}
        >
          <div className="card-title">
            <h2>{upload ? '上传影像' : '创建影像'}</h2>
            <button disabled={busy} onClick={() => patch({ mode: null })} type="button">
              收起
            </button>
          </div>
          <p className="muted">
            {upload
              ? '保留原始文件，自动分析内容并优先关联已有素材。符合要求的新素材可整理入库；存在歧义时再由你确认。'
              : '选择已有素材制作，提交后可在制作工作项中查看进度。'}
          </p>
          <label>
            影像类型
            <select
              disabled={busy}
              onChange={event => {
                const assetKind = event.target.value
                if (assetKind === 'actor' || assetKind === 'scene' || assetKind === 'cinematic') {
                  patch({ assetKind })
                }
              }}
              value={draft.assetKind}
            >
              <option value="cinematic">完整场景</option>
              <option value="scene">环境影像</option>
              <option value="actor">透明主体动画</option>
            </select>
          </label>
          {upload ? (
            <>
              <label>
                选择图片或视频
                <input
                  accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov"
                  disabled={busy}
                  onChange={event => setFile(event.target.files?.[0] ?? null)}
                  type="file"
                />
              </label>
              {file && <p className="muted">{file.name}</p>}
              {file &&
                preview &&
                (file.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(file.name) ? (
                  <video controls src={preview} style={{ maxHeight: 300, width: '100%' }} />
                ) : (
                  <img
                    alt={file.name}
                    src={preview}
                    style={{ maxHeight: 300, maxWidth: '100%', objectFit: 'contain' }}
                  />
                ))}
              <label>
                标题
                <input
                  disabled={busy}
                  maxLength={160}
                  onChange={event => patch({ title: event.target.value })}
                  value={draft.title}
                />
              </label>
              <label>
                描述
                <textarea
                  disabled={busy}
                  maxLength={1500}
                  onChange={event => patch({ description: event.target.value })}
                  value={draft.description}
                />
              </label>
            </>
          ) : (
            <>
              {draft.assetKind !== 'scene' && selectMaterial('着装', 'outfitId', materials.outfits)}
              {draft.assetKind !== 'actor' && selectMaterial('环境', 'sceneId', materials.scenes)}
              {draft.assetKind !== 'scene' && selectMaterial('动作', 'actionId', materials.actions)}
              {draft.assetKind === 'cinematic' && (
                <label>
                  媒体类型
                  <select
                    disabled={busy}
                    onChange={event => patch({ mediaKind: event.target.value === 'video' ? 'video' : 'image' })}
                    value={draft.mediaKind}
                  >
                    <option value="image">图片</option>
                    <option value="video">视频</option>
                  </select>
                </label>
              )}
              <label>
                创作要求
                <textarea
                  disabled={busy}
                  maxLength={2000}
                  onChange={event => patch({ requirements: event.target.value })}
                  value={draft.requirements}
                />
              </label>
              {!visualCreationReady(draft) && <p className="muted">请先准备所需的着装、环境与已入库动作。</p>}
            </>
          )}
          {materialError && !upload ? <Notice error={materialError} /> : null}
          {error ? <Notice error={error} /> : null}
          <div className="button-row">
            <button disabled={busy || (upload ? !file : !visualCreationReady(draft))} type="submit">
              {busy ? (upload ? '保存原件中…' : '提交制作中…') : upload ? '加入影像库' : '开始制作'}
            </button>
          </div>
        </form>
      )}
    </>
  )
}
