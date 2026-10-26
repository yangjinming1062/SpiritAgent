import { IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { VoiceSelectionResponse } from '@protocol'

import type { ApiSchemas } from '../shared/api.generated'

import type { RemoteApi } from './api'
import { newRequestId } from './browser'
import { Empty, FileInput, Media, Notice, PageHeader, useAlive, usePageState, useTask } from './ui'

type VoiceCatalog = ApiSchemas['VoicesListResponse']
type VoicePreview = ApiSchemas['VoicePreviewResponse']
type VoiceSample = ApiSchemas['VoiceSampleResponse']
type CustomVoice = ApiSchemas['CustomVoiceSummary']

const PREVIEW_TEXT = '你好呀，这是我的声音～'

type VoiceTab = 'clone' | 'design' | 'fixed'

function customVoiceLabel(voice: CustomVoice): string {
  return voice.kind === 'design' ? '设计音色' : '克隆声音'
}

function previewUrl(preview: VoicePreview): string {
  return `data:${preview.trial_audio_mime};base64,${preview.trial_audio_base64}`
}

export function Voice({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, busy, error } = useTask()
  const [catalog, setCatalog] = useState<VoiceCatalog | null>(null)
  const [current, setCurrent] = useState<VoiceSelectionResponse | null>(null)
  const [tab, setTab] = usePageState<VoiceTab>('voice.tab', 'fixed')
  const [query, setQuery] = usePageState('voice.query', '')
  const [prompt, setPrompt] = usePageState('voice.design.prompt', '')
  const [preview, setPreview] = useState<VoicePreview | null>(null)
  const [sample, setSample] = useState<VoiceSample | null>(null)
  const [trial, setTrial] = useState('')
  const [activeOperation, setActiveOperation] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const trialRef = useRef('')

  const replaceTrial = (url: string) => {
    if (trialRef.current.startsWith('blob:')) {
      URL.revokeObjectURL(trialRef.current)
    }
    trialRef.current = url
    setTrial(url)
  }

  useEffect(
    () => () => {
      if (trialRef.current.startsWith('blob:')) {
        URL.revokeObjectURL(trialRef.current)
      }
    },
    []
  )

  const load = useCallback(async () => {
    const [list, selection] = await Promise.all([
      api.invoke('voices.list_voices', {}),
      api.invoke('voices.get_current_voice', {})
    ])

    if (alive()) {
      setCatalog(list)
      setCurrent(selection)
      setLoaded(true)
    }
  }, [alive, api])

  useEffect(() => {
    void run(load)
  }, [load, refresh, run])

  const operationTask = (task: (operationId: string) => Promise<void>) =>
    run(async () => {
      const operationId = newRequestId()
      setActiveOperation(operationId)
      try {
        await task(operationId)
      } finally {
        if (alive()) {
          setActiveOperation(null)
        }
      }
    })

  const currentLabel = (): string => {
    if (!current?.voice_id) {
      return '尚未选择'
    }
    const fixed = catalog?.voices.find(voice => voice.id === current.voice_id)
    const custom = catalog?.custom_voices.find(voice => voice.voice_id === current.voice_id)
    return fixed?.label || (custom ? customVoiceLabel(custom) : current.voice_id)
  }

  const adoptPreview = () =>
    run(async () => {
      if (!preview) {
        return
      }
      await api.invoke('voices.post_voice_confirmation', {
        path: { candidate_id: preview.candidate_id },
        body: { expected_version: preview.expected_version }
      })
      await load()
    })

  return (
    <>
      <PageHeader subtitle="选择固定音色，或为伙伴设计、克隆一个专属声音" title="声音" />
      <section className="card">
        <h3>当前声音</h3>
        <p aria-live="polite">{currentLabel()}</p>
        {trial ? <Media type="audio" url={trial} /> : null}
      </section>
      {error ? <Notice>{error}</Notice> : null}
      <nav aria-label="声音来源" className="section-tabs">
        {(
          [
            { id: 'fixed', label: '选择音色' },
            { id: 'design', label: '设计音色' },
            { id: 'clone', label: '克隆声音' }
          ] as const
        ).map(item => (
          <button
            aria-current={tab === item.id ? 'page' : undefined}
            className={tab === item.id ? 'selected' : ''}
            key={item.id}
            onClick={() => setTab(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>
      {tab === 'fixed' ? (
        <>
          <div className="library-toolbar">
            <input
              aria-label="搜索音色"
              onChange={event => setQuery(event.target.value)}
              placeholder="搜索音色"
              type="search"
              value={query}
            />
            <button
              aria-label="刷新音色"
              className="icon-button"
              disabled={busy}
              onClick={() => run(load)}
              type="button"
            >
              <IconRefresh size={20} />
            </button>
          </div>
          <div className="memory-list">
            {catalog?.voices
              .filter(
                voice =>
                  !query.trim() || `${voice.label}${voice.description}${voice.tags.join('')}`.includes(query.trim())
              )
              .map(voice => (
                <article className="memory-item" key={voice.id}>
                  <div className="memory-summary">
                    <strong>{voice.label}</strong>
                    <span>{[voice.gender, voice.language, ...voice.tags].filter(Boolean).join(' · ')}</span>
                    {voice.description ? <span>{voice.description}</span> : null}
                  </div>
                  <div className="button-row">
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          const audio = await api.invoke('media.text_to_speech', {
                            body: { text: PREVIEW_TEXT, voice: voice.id, language: voice.language || undefined }
                          })
                          if (alive()) {
                            replaceTrial(URL.createObjectURL(new Blob([new Uint8Array(audio)], { type: 'audio/mpeg' })))
                          }
                        })
                      }
                      type="button"
                    >
                      试听
                    </button>
                    <button
                      className="primary"
                      disabled={busy || current?.voice_id === voice.id}
                      onClick={() =>
                        run(async () => {
                          await api.invoke('voices.put_current_voice', { body: { voice_id: voice.id } })
                          await load()
                        })
                      }
                      type="button"
                    >
                      {current?.voice_id === voice.id ? '已使用' : '使用这个声音'}
                    </button>
                  </div>
                </article>
              ))}
          </div>
          {loaded && !catalog?.voices.length ? <Empty title="暂无音色">当前语言暂时没有可选的固定音色。</Empty> : null}
        </>
      ) : tab === 'design' ? (
        <section className="creation-screen">
          {!catalog?.supports_voice_design ? <Notice>音色设计暂未配置，可以选择固定音色或克隆声音。</Notice> : null}
          <p className="muted">描述想要的音色，生成试听满意后再使用。</p>
          <label className="field-label">
            声音描述
            <textarea
              disabled={busy}
              maxLength={2000}
              onChange={event => setPrompt(event.target.value)}
              placeholder="例如：温柔的年轻女声，语速不急不缓，像午后聊天"
              rows={4}
              value={prompt}
            />
          </label>
          <div className="button-row">
            {catalog?.supports_voice_prompt_generation ? (
              <button
                disabled={busy || !prompt.trim()}
                onClick={() =>
                  operationTask(async operationId => {
                    const result = await api.invoke('voices.post_voice_prompt', {
                      body: { current_prompt: prompt.trim(), operation_id: operationId, preference: prompt.trim() }
                    })
                    if (alive()) {
                      setPrompt(result.prompt)
                    }
                  })
                }
                type="button"
              >
                润色声音描述
              </button>
            ) : null}
            <button
              className="primary"
              disabled={busy || !prompt.trim()}
              onClick={() =>
                operationTask(async operationId => {
                  const result = await api.invoke('voices.post_voice_candidate', {
                    body: {
                      kind: 'design',
                      operation_id: operationId,
                      preview_text: PREVIEW_TEXT,
                      prompt: prompt.trim()
                    }
                  })
                  if (alive()) {
                    setPreview(result)
                    replaceTrial(previewUrl(result))
                  }
                })
              }
              type="button"
            >
              生成试听
            </button>
          </div>
          {preview && preview.kind === 'design' ? (
            <div className="creation-status">
              <p>可以再听一次，满意后使用；也可以继续调整描述。</p>
              <button className="primary full" disabled={busy} onClick={adoptPreview} type="button">
                使用这个声音
              </button>
            </div>
          ) : null}
        </section>
      ) : (
        <section className="creation-screen">
          {!catalog?.supports_voice_clone ? <Notice>声音克隆暂未配置，可以选择固定音色或设计音色。</Notice> : null}
          <p className="muted">上传一段伙伴的参考声音，生成试听满意后再使用。</p>
          <div className="upload-area">
            <button disabled={busy} onClick={() => picker.current?.click()} type="button">
              {sample ? '换一段参考声音' : '选择参考声音'}
            </button>
            {sample ? <Media type="audio" url={sample.url} /> : null}
            <p className="muted">常见音频格式，具体大小限制以服务端为准</p>
          </div>
          <div className="button-row">
            <button
              className="primary"
              disabled={busy || !sample}
              onClick={() =>
                operationTask(async operationId => {
                  if (!sample) {
                    return
                  }
                  const result = await api.invoke('voices.post_voice_candidate', {
                    body: {
                      kind: 'clone',
                      operation_id: operationId,
                      preview_text: PREVIEW_TEXT,
                      sample_id: sample.sample_id
                    }
                  })
                  if (alive()) {
                    setPreview(result)
                    replaceTrial(previewUrl(result))
                  }
                })
              }
              type="button"
            >
              生成试听
            </button>
          </div>
          {preview && preview.kind === 'clone' ? (
            <div className="creation-status">
              <p>可以再听一次，满意后使用；也可以换一段参考声音。</p>
              <button className="primary full" disabled={busy} onClick={adoptPreview} type="button">
                使用这个声音
              </button>
            </div>
          ) : null}
        </section>
      )}
      {activeOperation ? (
        <div className="button-row">
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                if (activeOperation) {
                  await api.invoke('voices.delete_voice_operation', { path: { operation_id: activeOperation } })
                }
              })
            }
            type="button"
          >
            取消进行中的声音操作
          </button>
        </div>
      ) : null}
      {catalog?.custom_voices.length ? (
        <section className="card">
          <h3>已保存的声音</h3>
          {catalog.custom_voices.map(voice => (
            <article className="memory-item" key={voice.candidate_id}>
              <div className="memory-summary">
                <strong>{customVoiceLabel(voice)}</strong>
                {voice.prompt ? <span>{voice.prompt}</span> : null}
              </div>
              {voice.preview_audio_url ? <Media type="audio" url={voice.preview_audio_url} /> : null}
              <div className="button-row">
                <button
                  className="primary"
                  disabled={busy || current?.voice_id === voice.voice_id}
                  onClick={() =>
                    run(async () => {
                      await api.invoke('voices.post_voice_confirmation', {
                        path: { candidate_id: voice.candidate_id },
                        body: { expected_version: voice.version }
                      })
                      await load()
                    })
                  }
                  type="button"
                >
                  {current?.voice_id === voice.voice_id ? '已使用' : '使用这个声音'}
                </button>
              </div>
            </article>
          ))}
        </section>
      ) : null}
      <FileInput
        accept="audio/*"
        onFiles={([file]) => {
          if (file) {
            void run(async () => {
              const body = new FormData()
              body.append('file', file)
              const result = await api.invoke('media.upload_voice_reference', { body })
              if (alive()) {
                setSample(result)
                setPreview(null)
              }
            })
          }
        }}
        ref={picker}
      />
    </>
  )
}
