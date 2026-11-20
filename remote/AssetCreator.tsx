import { IconPhoto } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useRef } from 'react'

import { uiCopy } from '../shared/ui-copy'

import { type RemoteApi } from './api'
import { useAssetSubmission } from './asset-submission'
import { copyText, readImage } from './browser'
import { usePageState } from './page-state'
import { useAlive, useTask } from './tasks'
import type { Outfit, Scene } from './types'
import { AssetSteps, FileInput, Notice } from './ui'

export function AssetPolicyToggle({
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

/** 制作完成的素材实体，按类型区分供调用方直接落地，免二次读取。 */
type CreatedAsset = { kind: 'scene'; value: Scene } | { kind: 'outfit'; value: Outfit }

export function AssetCreator({
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
  onCreated: (created: CreatedAsset) => void
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
  const label = scene ? '环境' : '衣服'
  const imageBody = image ? { image: image.base64, content_type: image.type } : {}

  const create = async (): Promise<void> => {
    if (source === 'upload' && !image) {
      return
    }
    const notes = description.trim() || undefined
    const upload = source === 'upload' ? image : null
    await submit(
      async (): Promise<CreatedAsset> => {
        if (scene) {
          const value = await api.invoke('companion_scenes.create_scene', {
            body: upload
              ? { source: 'analysis', image: upload.base64 }
              : { source: 'generate', notes, image: image?.base64 }
          })
          return { kind: 'scene', value }
        }
        const value = await api.invoke('companion.post_outfit', {
          body: upload
            ? { source: 'analysis', description: notes, image: upload.base64, content_type: upload.type }
            : { source: 'generate', description: notes, ...imageBody }
        })
        return { kind: 'outfit', value }
      },
      created => {
        if (alive()) {
          setDescription('')
          setImage(null)
          setPrompt('')
          setSource('ai')
          onCreated(created)
        }
      }
    )
  }

  return (
    <section className="creation-screen">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      <h1>{source === 'ai' ? `让 AI 帮你设计${label}` : `使用自己的${label}图片`}</h1>
      <p className="muted">
        {source === 'ai' ? '用日常的话说说想法，完成后可以先预览。' : '上传已经做好的图片，确认后收入素材。'}
      </p>
      <AssetSteps step={1} upload={source === 'upload'} />
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
          onClick={() => run(create)}
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
                  await submit(
                    () =>
                      api.invoke('companion_scenes.post_scene_prompt', {
                        body: { notes: description.trim() || undefined }
                      }),
                    result => {
                      if (alive()) {
                        setPrompt(result.prompt)
                        setSource('ai')
                        onCreated({ kind: 'scene', value: result })
                      }
                    }
                  )
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
              <button onClick={() => run(() => copyText(prompt))} type="button">
                复制说明
              </button>
            </>
          ) : null}
        </details>
      ) : null}
      <FileInput
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
