import { useState } from 'react'
import type { ReactElement } from 'react'

import type { VisualDetail, VisualInterpretation } from '@protocol'

import { composeDescribedInput } from '../shared/described-input'
import { InterpretationGuard } from '../shared/interpretation-guard'
import { isStaleDraftError } from '../shared/visuals'

import { ApiError, type RemoteApi } from './api'
import { Notice, useAlive, usePageState, useTask } from './ui'

type Intent = 'photo' | 'video' | 'motion'

const INTENTS = [
  { id: 'photo' as const, title: '一张照片', hint: '记录此刻的画面' },
  { id: 'video' as const, title: '一段视频', hint: '让画面动起来' },
  { id: 'motion' as const, title: '让它动起来', hint: '用当前造型演一段动作' }
]

interface Studio {
  description: string
  intent: Intent
  preview: VisualInterpretation | null
  refinement: string
  requestId: string | null
  title: string
  uploadDescription: string
}

const EMPTY_STUDIO: Studio = {
  description: '',
  intent: 'photo',
  preview: null,
  refinement: '',
  requestId: null,
  title: '',
  uploadDescription: ''
}

const interpretation = new InterpretationGuard()

const STEP_LABEL: Record<string, string> = {
  create_action: '先创建一个动作',
  create_outfit: '先准备一套造型',
  create_scene: '先创建一个环境'
}

/** 创作影像：描述 → 预览 → 确认；确认后才受理制作。上传原件是独立入口。 */
export function VisualCreate({
  api,
  onDone
}: {
  api: RemoteApi
  onDone: (created?: VisualDetail) => Promise<void>
}): ReactElement {
  const alive = useAlive()
  const [mode, setMode] = usePageState<'idle' | 'describe' | 'upload'>('visuals.creationMode', 'idle')
  const [studio, setStudio] = usePageState<Studio>('visuals.studio', EMPTY_STUDIO)
  const [file, setFile] = usePageState<File | null>('visuals.uploadFile', null, false)
  const [previewUrl, setPreviewUrl] = useState('')
  const { run, busy, error } = useTask()
  const patch = (value: Partial<Studio>): void => {
    // 草稿任何变化都推进整理修订，在途整理的响应不再落地。
    interpretation.revise()
    setStudio(previous => ({ ...previous, ...value }))
  }
  const reset = (): void => {
    interpretation.revise()
    setStudio(EMPTY_STUDIO)
  }

  const describe = async (): Promise<void> => {
    const description = composeDescribedInput(studio.description, studio.refinement)

    if (!description) {
      return
    }

    // 结果只在请求代次与草稿修订都未变时落地：改描述、换类型或收起面板都会作废在途整理。
    const accept = interpretation.begin()

    try {
      const result: VisualInterpretation = await api.invoke('companion_visuals.interpret_visual', {
        body: { description, intent: studio.intent }
      })

      if (alive() && accept()) {
        patch({ description, preview: result, refinement: '', requestId: null })
      }
    } catch (failure) {
      // 过期失败不替换当前提示，也不清草稿。
      if (alive() && accept()) {
        throw failure
      }
    }
  }

  const confirm = async (): Promise<void> => {
    const plan = studio.preview

    if (!plan?.draft) {
      return
    }

    const request_id = studio.requestId ?? crypto.randomUUID()
    patch({ requestId: request_id })

    try {
      await api.invoke('companion_visuals.create_visual', { body: { request_id, draft: plan.draft } })
    } catch (failure) {
      if (alive() && failure instanceof ApiError && isStaleDraftError(failure.code)) {
        patch({ preview: null })
      }

      throw failure
    }

    if (alive()) {
      reset()
      setMode('idle')
      await onDone()
    }
  }

  const upload = async (): Promise<void> => {
    if (!file) {
      return
    }

    const bytes = new Uint8Array(await file.arrayBuffer())

    if (!alive()) {
      return
    }

    const result = await api.invoke('companion_visuals.upload_visual', {
      body: bytes,
      query: { filename: file.name, title: studio.title, description: studio.uploadDescription }
    })

    if (alive()) {
      setFile(null)
      setPreviewUrl(previous => {
        if (previous) {
          URL.revokeObjectURL(previous)
        }

        return ''
      })
      patch({ title: '', uploadDescription: '' })
      setMode('idle')
      await onDone(result)
    }
  }

  const plan = studio.preview

  return (
    <>
      <div className="button-row">
        <button disabled={busy} onClick={() => setMode('describe')} type="button">
          创建影像
        </button>
        <button disabled={busy} onClick={() => setMode('upload')} type="button">
          上传影像
        </button>
      </div>
      {mode === 'upload' && (
        <form
          className="card"
          onSubmit={event => {
            event.preventDefault()
            void run(upload)
          }}
        >
          <div className="card-title">
            <h2>上传影像</h2>
            <button disabled={busy} onClick={() => setMode('idle')} type="button">
              收起
            </button>
          </div>
          <p className="muted">保留原始文件，自动分析内容并优先关联已有素材。存在歧义时再由你确认。</p>
          <label>
            选择图片或视频
            <input
              accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov"
              disabled={busy}
              onChange={event => {
                const next = event.target.files?.[0] ?? null
                setFile(next)
                setPreviewUrl(previous => {
                  if (previous) {
                    URL.revokeObjectURL(previous)
                  }

                  return next ? URL.createObjectURL(next) : ''
                })
              }}
              type="file"
            />
          </label>
          {file && <p className="muted">{file.name}</p>}
          <label>
            标题
            <input
              disabled={busy}
              maxLength={160}
              onChange={event => patch({ title: event.target.value })}
              value={studio.title}
            />
          </label>
          <label>
            描述
            <textarea
              disabled={busy}
              maxLength={1500}
              onChange={event => patch({ uploadDescription: event.target.value })}
              rows={3}
              value={studio.uploadDescription}
            />
          </label>
          {previewUrl &&
            (file?.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(file?.name ?? '') ? (
              <video controls src={previewUrl} style={{ maxHeight: 300, width: '100%' }} />
            ) : (
              <img
                alt={file?.name}
                src={previewUrl}
                style={{ maxHeight: 300, maxWidth: '100%', objectFit: 'contain' }}
              />
            ))}
          {error ? <Notice error={error} /> : null}
          <div className="button-row">
            <button disabled={busy || !file || !studio.title.trim()} type="submit">
              {busy ? '保存原件中…' : '加入影像库'}
            </button>
          </div>
        </form>
      )}
      {mode === 'describe' && (
        <form
          className="card"
          onSubmit={event => {
            event.preventDefault()
            void run(plan?.draft ? confirm : describe)
          }}
        >
          <div className="card-title">
            <h2>创建影像</h2>
            <button
              disabled={busy}
              onClick={() => {
                reset()
                setMode('idle')
              }}
              type="button"
            >
              收起
            </button>
          </div>
          <p className="muted">描述你想要的画面，确认后再开始制作。</p>
          {!plan && (
            <>
              <fieldset className="intent-row">
                <legend>想做什么</legend>
                {INTENTS.map(item => (
                  <label key={item.id}>
                    <input
                      checked={studio.intent === item.id}
                      disabled={busy}
                      name="visual-intent"
                      onChange={() => patch({ intent: item.id })}
                      type="radio"
                    />
                    {item.title}
                    <span className="muted">（{item.hint}）</span>
                  </label>
                ))}
              </fieldset>
              <label>
                描述你想要的画面
                <textarea
                  disabled={busy}
                  onChange={event => patch({ description: event.target.value })}
                  placeholder="例如：穿着舒适的家居服，在窗边安静地读书"
                  rows={3}
                  value={studio.description}
                />
              </label>
            </>
          )}
          {plan && (
            <div aria-live="polite">
              <h3>{plan.feasible ? '这样理解对吗' : '暂时做不了'}</h3>
              {plan.feasible ? (
                <>
                  <p>
                    <strong>{plan.summary}</strong>
                  </p>
                  <dl>
                    {plan.details.map(item => (
                      <div key={`${item.label}:${item.value}`}>
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                  {plan.materials.some(material => material.preview_url) ? (
                    <div className="material-previews">
                      {plan.materials
                        .filter(material => material.preview_url)
                        .map(material => (
                          <figure key={material.id}>
                            <img alt={material.name} src={material.preview_url ?? ''} />
                            <figcaption>{material.name}</figcaption>
                          </figure>
                        ))}
                    </div>
                  ) : null}
                </>
              ) : (
                <>
                  <p>{plan.reason}</p>
                  {plan.next_step ? <p className="muted">{STEP_LABEL[plan.next_step]}</p> : null}
                </>
              )}
              <label>
                还需要补充什么
                <input
                  disabled={busy}
                  onChange={event => patch({ refinement: event.target.value })}
                  placeholder="例如：光线再柔和一些"
                  value={studio.refinement}
                />
              </label>
            </div>
          )}
          {error ? <Notice error={error} /> : null}
          <div className="button-row">
            <button disabled={busy || (!plan && !studio.description.trim())} type="submit">
              {busy ? '整理中…' : plan ? (plan.draft ? '开始制作' : '再说一句') : '整理描述'}
            </button>
            {plan && (
              <button disabled={busy} onClick={() => void run(describe)} type="button">
                再说一句
              </button>
            )}
          </div>
        </form>
      )}
    </>
  )
}
