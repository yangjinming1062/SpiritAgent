import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_APPEARANCE, resolvePortraitUrl } from '@/companion'
import { HistoryGallery, PortraitLightbox, useNaturalAspectRatio } from '@/shared'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, WizardModal } from '@/shared/panel'

const HISTORY_CAP = 5

interface Seed3dWizardProps {
  avatarId: number
  /** 供应商是否按多图模式消费种子图（决定背面阶段是否存在）。 */
  supportsMultiview: boolean
  /** 确认 3D 种子（或选择仅用正面图降级）后由调用方切换渲染模式并关闭向导。 */
  onConfirm: () => void
  onCancel: () => void
}

type Stage = 'front' | 'back'

interface StageState {
  rawUrl: string | null
  previewUrl: string | null
  entries: { rawUrl: string; previewUrl: string }[]
  idx: number
  loading: boolean
  failed: boolean
}

const EMPTY_STAGE: StageState = { rawUrl: null, previewUrl: null, entries: [], idx: 0, loading: false, failed: false }

const STAGE_META: Record<
  Stage,
  {
    endpoint: string
    field: 'seed_front_3d_url' | 'seed_back_url'
    title: string
    hint: string
    alt: string
    placeholder: string
    genFail: string
    loadFail: string
  }
> = {
  front: {
    endpoint: '/fullbody/front-3d',
    field: 'seed_front_3d_url',
    title: '升级 3D：确认 3D 正面立绘',
    hint: '3D 建模需要标准站姿（A-pose）与 3D 画风的正面图，以你的形象头像为基准生成；不满意可微调重绘。',
    alt: '3D 正面全身立绘',
    placeholder: '对 3D 正面立绘有微调要求？例如：姿势细节、服饰纹理…（可留空直接下一步）',
    genFail: '生成 3D 正面立绘失败，请稍后重试',
    loadFail: '正在生成 3D 正面立绘…'
  },
  back: {
    endpoint: '/fullbody/back',
    field: 'seed_back_url',
    title: '升级 3D：确认背面立绘',
    hint: '3D 多视角建模以刚确认的 3D 正面立绘为基准补一张背面视图；不满意可微调重绘。',
    alt: '背面全身立绘',
    placeholder: '对背面立绘有微调要求？例如：发型细节、背部服饰/配饰…（可留空直接确认）',
    genFail: '生成背面立绘失败，请稍后重试',
    loadFail: '正在生成背面立绘…'
  }
}

// 3D 升级前的种子图确认向导（DESIGN §5.5）：3D 画风与站姿要求与 2D 立绘不同——先以已确认
// 的 2D 正面种子为基准生成 A-pose 的 3D 正面立绘，多视角供应商再从它派生背面立绘，两者都
// 只在用户明确选择 3D 时生成。生成失败可降级为仅正面图提交。
export function Seed3dWizard({
  avatarId,
  supportsMultiview,
  onConfirm,
  onCancel
}: Seed3dWizardProps): React.ReactElement {
  const [stage, setStage] = useState<Stage>('front')

  const [stages, setStages] = useState<Record<Stage, StageState>>({
    front: EMPTY_STAGE,
    back: EMPTY_STAGE
  })

  const [feedback, setFeedback] = useState<Record<Stage, string>>({ front: '', back: '' })
  const [hint, setHint] = useState<string | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const generatingRef = useRef(false)

  useEffect(() => {
    // StrictMode 开发态会卸载重挂一次，cleanup 已把标记置 false——重挂时必须复位，
    // 否则 boot 里所有 mountedRef 守卫全部失效，向导永久停在加载态。
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  const patchStage = useCallback((key: Stage, patch: Partial<StageState>): void => {
    setStages(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }, [])

  const generate = useCallback(
    async (key: Stage, feedbackText: string): Promise<boolean> => {
      if (generatingRef.current) {
        return false
      }

      const meta = STAGE_META[key]
      generatingRef.current = true
      patchStage(key, { loading: true, failed: false })
      setHint(null)

      try {
        const res = await window.spiritagent.api<{ seed_front_3d_url?: string | null; seed_back_url?: string | null }>({
          path: `/api/companion/avatar/${avatarId}${meta.endpoint}`,
          method: 'POST',
          body: { feedback: feedbackText.trim() || undefined }
        })

        const raw = res?.[meta.field] || null
        const resolved = raw ? await resolvePortraitUrl(raw) : null

        if (!raw || !resolved) {
          throw new Error(meta.genFail)
        }

        if (!mountedRef.current) {
          return true
        }

        setStages(prev => {
          const cur = prev[key]
          const entries = [...cur.entries, { rawUrl: raw, previewUrl: resolved }]
          const capped = entries.length > HISTORY_CAP ? entries.slice(entries.length - HISTORY_CAP) : entries

          return {
            ...prev,
            [key]: {
              ...cur,
              rawUrl: raw,
              previewUrl: resolved,
              entries: capped,
              idx: capped.length - 1,
              loading: false,
              failed: false
            }
          }
        })

        // 正面重绘会使后端已派生的背面种子失效，本地同步作废（由用户在背面阶段重新点按生成）
        if (key === 'front') {
          setStages(prev => ({ ...prev, back: EMPTY_STAGE }))
        }

        return true
      } catch (err) {
        if (!mountedRef.current) {
          return false
        }

        patchStage(key, { loading: false, failed: true })
        setHint(err instanceof Error ? err.message : meta.genFail)

        return false
      } finally {
        generatingRef.current = false
      }
    },
    [avatarId, patchStage]
  )

  // 打开时水合已有 3D 种子（存量用户直接复用）；没有则停在空态，由用户点按显式触发——避免误触 3D 切换即消耗生图费用。
  useEffect(() => {
    const boot = async (): Promise<void> => {
      let existingFront: string | null = null
      let existingBack: string | null = null

      try {
        const res = await window.spiritagent.api<{ seed_front_3d_url?: string | null; seed_back_url?: string | null }>({
          path: '/api/companion/avatar'
        })

        existingFront = res?.seed_front_3d_url || null
        existingBack = res?.seed_back_url || null
      } catch {
        // 拉取失败不阻塞向导：当作没有 3D 种子，由用户点按显式触发。
      }

      if (!mountedRef.current) {
        return
      }

      if (existingFront) {
        const resolved = await resolvePortraitUrl(existingFront)

        if (mountedRef.current && resolved) {
          setStages(prev => ({
            ...prev,
            front: {
              ...prev.front,
              rawUrl: existingFront,
              previewUrl: resolved,
              entries: [{ rawUrl: existingFront, previewUrl: resolved }],
              idx: 0,
              loading: false,
              failed: false
            }
          }))

          if (supportsMultiview) {
            setStage('back')
          }

          if (existingBack) {
            const backResolved = await resolvePortraitUrl(existingBack)

            if (mountedRef.current && backResolved) {
              setStages(prev => ({
                ...prev,
                back: {
                  ...prev.back,
                  rawUrl: existingBack,
                  previewUrl: backResolved,
                  entries: [{ rawUrl: existingBack, previewUrl: backResolved }],
                  idx: 0,
                  loading: false,
                  failed: false
                }
              }))

              return
            }
          }

          return
        }
      }
    }

    void boot()
  }, [generate, supportsMultiview])

  // Esc 关闭向导；灯箱打开时让灯箱自己的 Esc 生效，不连带关掉整个向导。
  // 捕获阶段拦截并阻断冒泡，外层设置面板的 Esc 不连坐。
  useEscapeKey(onCancel, { busy: Boolean(zoomUrl) })

  const onSelectHistoryEntry = (key: Stage, idx: number): void => {
    const entry = stages[key].entries[idx]

    if (entry) {
      patchStage(key, { idx, rawUrl: entry.rawUrl, previewUrl: entry.previewUrl })
    }
  }

  const regenerate = (key: Stage): void => {
    void generate(key, feedback[key])
  }

  const current = stages[stage]
  const meta = STAGE_META[stage]
  // 全身画幅随物种骨骼分桶（竖/方/横并存）：取景框比例跟随当前立绘图片本身
  const frameRatio = useNaturalAspectRatio(current.previewUrl)

  return (
    <WizardModal
      escClose={false}
      onClose={onCancel}
      regionId="seed3d-wizard"
      title={
        <>
          {meta.title}
          {supportsMultiview && (
            <span className="ml-1.5 text-[11px] font-normal text-faint">{stage === 'front' ? '1 / 2' : '2 / 2'}</span>
          )}
        </>
      }
      widthClass="max-w-sm"
    >
      <p className="text-[11px] leading-relaxed text-muted">{meta.hint}</p>

      <div
        className="relative mx-auto mt-3 flex aspect-[9/16] max-h-[320px] w-auto items-center justify-center overflow-hidden rounded-xl border border-line-standard bg-black/40 group"
        style={frameRatio ? { aspectRatio: frameRatio } : undefined}
      >
        {current.previewUrl ? (
          <button
            aria-label="放大查看"
            className="relative block h-full w-full cursor-zoom-in overflow-hidden border-0 bg-transparent p-0"
            onClick={() => setZoomUrl(current.previewUrl)}
            type="button"
          >
            <img alt={meta.alt} className="h-full w-full object-contain" src={current.previewUrl ?? undefined} />
          </button>
        ) : current.loading ? (
          <div className="text-xs text-faint">{meta.loadFail}</div>
        ) : current.failed ? (
          <div className="text-xs text-faint">暂无立绘</div>
        ) : (
          <div className="flex flex-col items-center gap-3 px-4">
            <div className="text-xs text-muted">{stage === 'front' ? '尚未生成 3D 正面立绘' : '尚未生成背面立绘'}</div>
            <button className={BTN_PRIMARY} onClick={() => generate(stage, '')} type="button">
              {stage === 'front' ? '生成 3D 正面立绘' : '生成背面立绘'}
            </button>
          </div>
        )}
      </div>

      {current.entries.length > 1 && (
        <div className="mt-2">
          <HistoryGallery
            entries={current.entries.map(entry => ({ url: entry.previewUrl }))}
            onSelect={idx => onSelectHistoryEntry(stage, idx)}
            selectedIdx={current.idx}
          />
        </div>
      )}

      <div className="mt-3">
        <textarea
          className={cn(INPUT_CLASS, 'resize-none')}
          disabled={current.loading}
          maxLength={MAX_APPEARANCE}
          onChange={e => setFeedback(prev => ({ ...prev, [stage]: e.target.value }))}
          placeholder={meta.placeholder}
          rows={2}
          value={feedback[stage]}
        />
      </div>

      {hint && <p className="mt-2 text-xs text-rose-300/90">{hint}</p>}

      {current.failed && !current.previewUrl && (
        <div className="mt-3 flex items-center justify-between rounded-xl border border-line-hairline bg-surface-card px-3 py-2">
          <span className="text-muted">{stage === 'front' ? '3D 正面立绘生成失败' : '背面立绘生成失败'}</span>
          <div className="flex gap-2">
            <button
              className={cn(BTN_SUBTLE, 'h-7 px-3')}
              disabled={current.loading}
              onClick={() => regenerate(stage)}
              type="button"
            >
              重试
            </button>
            {stage === 'back' && (
              <button className={cn(BTN_SUBTLE, 'h-7 px-3')} onClick={onConfirm} type="button">
                仅用正面图生成 3D
              </button>
            )}
          </div>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between">
        <button
          className="rounded-lg px-2 py-1 text-xs text-muted transition hover:bg-fill-hover hover:text-strong disabled:opacity-40"
          disabled={current.loading}
          onClick={stage === 'back' ? () => setStage('front') : onCancel}
          type="button"
        >
          {stage === 'back' ? '返回正面' : '取消'}
        </button>
        <div className="flex items-center gap-2">
          <button
            className="rounded-lg px-2 py-1 text-xs text-body transition hover:bg-fill-hover hover:text-strong disabled:opacity-40"
            disabled={current.loading || !current.previewUrl}
            onClick={() => regenerate(stage)}
            type="button"
          >
            微调重绘
          </button>
          {stage === 'front' && supportsMultiview ? (
            <button
              className={BTN_PRIMARY}
              disabled={current.loading || !current.previewUrl}
              onClick={() => setStage('back')}
              type="button"
            >
              下一步：背面立绘
            </button>
          ) : (
            <button
              className={BTN_PRIMARY}
              disabled={current.loading || !current.previewUrl}
              onClick={onConfirm}
              type="button"
            >
              确认，切换到 3D
            </button>
          )}
        </div>
      </div>

      {zoomUrl && <PortraitLightbox name={meta.alt} onClose={() => setZoomUrl(null)} url={zoomUrl} />}
    </WizardModal>
  )
}
