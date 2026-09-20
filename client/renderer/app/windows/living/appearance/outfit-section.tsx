import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $avatarSeeds,
  $outfitPolicy,
  $outfits,
  activateOutfit,
  deleteOutfit,
  GenerationActionsGroup,
  hydrateAvatarSeeds,
  hydrateWardrobe,
  type PickedImage,
  resolvePortraitUrl,
  SelfSourceImageFlow,
  type SelfSourceReferenceImage,
  setOutfitPolicy,
  useOutfitDesignSession
} from '@/modules/character'
import { PortraitLightbox } from '@/shared'
import { ArrowBackUp, Check, FileImage, ImagePlus, Pencil, Plus, Send, Trash2 } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_ICON, BTN_PRIMARY, HINT_TEXT, INPUT_CLASS, Spinner, Toggle } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import type { ImageReviseMode } from '@/shared/types/spiritagent'

// 列表卡 hover 操作钮：浮在立绘上，深色半透明底保证任何画面下可读。
const CARD_ACTION_CLASS =
  'inline-flex h-6 items-center justify-center rounded-lg bg-black/60 px-1.5 text-white/70 backdrop-blur-sm transition hover:bg-black/80 hover:text-white disabled:pointer-events-none disabled:opacity-40'

// 外观页外观分区（DESIGN §6.1）：左侧外观画廊（政策开关 + 设计入口 + 卡片流），
// 右侧大图展示穿着中/选中外观；「设计新装」进入设计态后底部展开全宽设计抽屉，
// 描述 / 参考图 / 微调反馈 / 确认入柜都在抽屉内完成。确认只表示参考图就绪，不触发生成。
export function OutfitSection(): React.JSX.Element {
  const outfits = useStore($outfits)
  const outfitPolicy = useStore($outfitPolicy)
  const avatarSeeds = useStore($avatarSeeds)
  const dict = useStrings()
  const t = dict.living.outfit
  const common = dict.common
  const selfSourceDict = dict.selfSource
  const [busyId, setBusyId] = useState<number | null>(null)
  const [policyBusy, setPolicyBusy] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [designing, setDesigning] = useState(false)
  const [text, setText] = useState('')
  const [outfitSelfSourceOpen, setOutfitSelfSourceOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)

  // 确认入柜后重拉列表——新装转为参考图就绪。
  const session = useOutfitDesignSession(() => {
    setDesigning(false)
    setText('')
    void hydrateWardrobe()
  })

  // 选中项被删除 / 列表刷新后兜底回落到穿着中（或第一项）。
  useEffect(() => {
    if (!outfits.some(o => o.id === selectedId)) {
      setSelectedId(outfits.find(o => o.active)?.id ?? outfits[0]?.id ?? null)
    }
  }, [outfits, selectedId])

  useEffect(() => {
    messagesRef.current?.scrollTo?.({ top: messagesRef.current.scrollHeight })
  }, [session.messages])

  const withBusy = (id: number, action: () => Promise<unknown>): void => {
    setBusyId(id)
    void action().finally(() => setBusyId(null))
  }

  // 失败外观重新确认（草稿立绘仍在）：转正为参考图就绪。
  const retryConfirm = async (id: number): Promise<void> => {
    try {
      // 与设计会话确认一致：始终带 JSON body（可空），避免无 body 的 POST 被 422。
      await window.spiritagent.api({ path: `/api/companion/outfits/${id}/confirm`, method: 'POST', body: {} })
      await hydrateWardrobe()
    } catch (err) {
      log.warn('outfit', 'retry confirm failed', err)
    }
  }

  const startDesign = (): void => {
    if (session.busy) {
      return
    }

    session.reset()
    setDesigning(true)
    setSelectedId(null)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const togglePolicy = async (): Promise<void> => {
    setPolicyBusy(true)
    const next = outfitPolicy === 'locked' ? 'llm_may_replace' : 'locked'
    await setOutfitPolicy(next)
    setPolicyBusy(false)
  }

  const selected = outfits.find(o => o.id === selectedId) ?? null
  const previewUrl = designing ? session.draft?.previewUrl : (selected?.fullbodyUrl ?? null)

  // 首次生成（无草稿）：描述/参考图创建新设计。
  const sendCreation = (): void => {
    // 生成进行中会话内部会拒绝——此时不清空输入，避免丢字。
    if (session.busy || (!text.trim() && !session.refImage)) {
      return
    }

    session.send(text, 'edit')
    setText('')
  }

  // 草稿反馈的两个显式操作（DESIGN §5.4）：微调编辑上一版须带反馈，重新生成允许空反馈整体重绘。
  const sendRevise = (mode: ImageReviseMode): void => {
    if (session.busy || (mode === 'edit' && !text.trim())) {
      return
    }

    session.send(text, mode)
    setText('')
  }

  // 自备图（外观立绘）：有草稿时提示词/采纳都走草稿重绘语境，否则走创建语境。
  const fetchOutfitSelfSourcePrompt = async (): Promise<string> => {
    if (session.draft) {
      const res = await window.spiritagent.api<{ prompt: string }>({
        path: `/api/companion/outfits/${session.draft.id}/prompt`,
        method: 'POST',
        body: { feedback: text.trim() || undefined }
      })

      return res.prompt
    }

    const res = await window.spiritagent.api<{ prompt: string }>({
      path: '/api/companion/outfits/prompt',
      method: 'POST',
      body: {
        description: text.trim() || undefined,
        image: session.refImage?.base64,
        content_type: session.refImage?.contentType
      }
    })

    return res.prompt
  }

  const adoptOutfitSelfSource = async (image: PickedImage): Promise<void> => {
    const res = await window.spiritagent.api<{ id?: number; fullbody_url?: string }>({
      path: session.draft ? `/api/companion/outfits/${session.draft.id}/adopt` : '/api/companion/outfits/adopt',
      method: 'POST',
      // 草稿重绘语境的采纳不收 description（schema forbid，描述沿用草稿 source_json 里的原文）。
      body: {
        image: image.base64,
        content_type: image.contentType,
        ...(session.draft ? {} : { description: text.trim() || undefined })
      }
    })

    void hydrateWardrobe()

    const resolved = res?.fullbody_url ? await resolvePortraitUrl(res.fullbody_url) : null

    // 从采纳后的草稿续上设计会话，可继续描述微调或直接确认入柜。
    if (res?.id) {
      session.adoptDraft(res.id, resolved ?? '')
    }

    setText('')
  }

  const outfitSelfSourceReferences: SelfSourceReferenceImage[] | undefined = avatarSeeds.fullbodySeedUrl
    ? [{ label: selfSourceDict.refs.fullbodySeed, url: avatarSeeds.fullbodySeedUrl }]
    : undefined

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1">
        {/* 左：外观画廊（政策开关 + 设计入口 + 方形大图卡流） */}
        <div className="flex w-60 shrink-0 flex-col border-r border-line-hairline">
          <div className="border-b border-line-hairline px-3 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-[11px] font-medium text-strong">{t.policyLabel}</p>
                <p className="mt-0.5 truncate text-[10px] text-faint">
                  {outfitPolicy === 'locked' ? t.policyStatusLocked : t.policyStatusUnlocked}
                </p>
              </div>
              <Toggle
                ariaLabel={t.policyToggleAria}
                checked={outfitPolicy !== 'locked'}
                disabled={policyBusy}
                onChange={() => void togglePolicy()}
              />
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed text-muted">{t.policyDesc}</p>
            <button
              className={cn(BTN_PRIMARY, 'mt-2.5 w-full')}
              disabled={session.busy}
              onClick={startDesign}
              type="button"
            >
              <Plus className="mr-1 size-3.5" />
              {t.startAction}
            </button>
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2.5 pt-4 pb-3">
            {outfits.length === 0 ? (
              <p className="px-1 pt-2 text-xs text-muted">{t.empty}</p>
            ) : (
              outfits.map(outfit => {
                const statusLabel = t.statusLabels[outfit.status] ?? ''
                const deletable = !outfit.active
                const isActiveCard = !designing && selectedId === outfit.id

                return (
                  <div
                    className={`group relative cursor-pointer overflow-hidden rounded-xl border text-left transition ${
                      isActiveCard
                        ? 'border-accent-line bg-accent-soft'
                        : 'border-line-hairline bg-surface-card hover:border-line-strong'
                    }`}
                    key={outfit.id}
                    onClick={() => {
                      setDesigning(false)
                      setSelectedId(outfit.id)
                    }}
                  >
                    <div className="relative aspect-square w-full bg-fill-trough">
                      {outfit.fullbodyUrl && (
                        <img
                          alt={outfit.name}
                          className="absolute inset-0 h-full w-full object-contain"
                          loading="lazy"
                          src={outfit.fullbodyUrl}
                        />
                      )}

                      {outfit.active && (
                        <span className="absolute left-1.5 top-1.5 rounded-md bg-emerald-500/85 px-1.5 py-0.5 text-[10px] font-medium text-white">
                          {t.wearing}
                        </span>
                      )}
                      {statusLabel && (
                        <span className="absolute bottom-1.5 left-1.5 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] text-white">
                          {statusLabel}
                        </span>
                      )}

                      <div className="absolute right-1.5 top-1.5 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
                        {(outfit.status === 'draft' || outfit.status === 'failed') && (
                          <button
                            aria-label={t.actions.continueDesign}
                            className={CARD_ACTION_CLASS}
                            onClick={e => {
                              e.stopPropagation()
                              session.adoptDraft(outfit.id, outfit.fullbodyUrl ?? '')
                              setDesigning(true)
                              setSelectedId(null)
                            }}
                            title={t.actions.continueDesignTitle}
                            type="button"
                          >
                            <Pencil className="size-3.5" />
                          </button>
                        )}
                        {outfit.status === 'ready' && !outfit.active && (
                          <button
                            aria-label={t.actions.wear}
                            className={CARD_ACTION_CLASS}
                            disabled={busyId === outfit.id}
                            onClick={e => {
                              e.stopPropagation()
                              withBusy(outfit.id, () => activateOutfit(outfit.id))
                            }}
                            title={t.actions.wearTitle}
                            type="button"
                          >
                            <Check className="size-3.5" />
                          </button>
                        )}
                        {outfit.status === 'failed' && (
                          <button
                            className={CARD_ACTION_CLASS}
                            disabled={busyId === outfit.id}
                            onClick={e => {
                              e.stopPropagation()
                              withBusy(outfit.id, () => retryConfirm(outfit.id))
                            }}
                            type="button"
                          >
                            {t.actions.retry}
                          </button>
                        )}
                        {deletable && (
                          <button
                            aria-label={t.actions.delete}
                            className={cn(CARD_ACTION_CLASS, 'hover:text-rose-300')}
                            disabled={busyId === outfit.id}
                            onClick={e => {
                              e.stopPropagation()
                              withBusy(outfit.id, () => deleteOutfit(outfit.id))
                            }}
                            title={t.actions.deleteTitle}
                            type="button"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="px-2.5 py-2">
                      <p className="truncate text-[11px] font-medium text-strong">{outfit.name}</p>
                      <p className="mt-0.5 truncate text-[10px] text-muted">{outfit.description}</p>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* 右：大图。固定方形取景框：全身图比例随物种而异，不假设方形——contain 完整
            呈现，长方图两侧留空。外层 inset 定位拿到确定的高宽（auto 高容器里
            百分比 max 解析不到，图会按内容自然高溢出可视区），内层 h-full +
            aspect-square 取可用区内最大正方形。 */}
        <div className="relative min-h-0 flex-1">
          {previewUrl ? (
            <div className="absolute inset-4 grid place-items-center">
              <button
                className="relative block aspect-square h-full max-w-full cursor-zoom-in overflow-hidden rounded-xl border border-line-hairline bg-fill-trough"
                onClick={() => setZoomUrl(previewUrl)}
                type="button"
              >
                <img alt={t.imageAlt} className="absolute inset-0 h-full w-full object-contain" src={previewUrl} />
              </button>
            </div>
          ) : (
            <div className="absolute inset-4 grid place-items-center text-center">
              <div>
                <div className="text-xs text-body">
                  {designing ? t.previewPlaceholderDesigning : t.previewPlaceholderIdle}
                </div>
                <div className="mt-1 text-[10px] text-faint">{t.previewHint}</div>
              </div>
            </div>
          )}

          {designing && session.busy && (
            <div className="absolute inset-4 flex flex-col items-center justify-center gap-2 rounded-xl bg-black/40">
              <Spinner className="size-5" />
              <span className="text-xs text-white">{t.generating}</span>
            </div>
          )}
        </div>
      </div>

      {/* 设计抽屉：进入设计态后展开，占满画廊与舞台以下的整行。 */}
      {designing && (
        <div className={cn('flex shrink-0 flex-col border-t border-line-hairline', session.draft ? 'h-80' : 'h-56')}>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3 text-xs" ref={messagesRef}>
            {session.messages.length === 0 && <p className="text-faint">{t.designIntro}</p>}
            {session.messages.map(m =>
              m.role === 'user' ? (
                <div className="flex justify-end" key={m.id}>
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent/20 px-3 py-1.5 text-strong">
                    {m.imageUrl && (
                      <button
                        aria-label={t.zoomRefImage}
                        className="mb-1 block cursor-zoom-in"
                        onClick={() => setZoomUrl(m.imageUrl ?? null)}
                        type="button"
                      >
                        <img
                          alt={t.refImageAlt}
                          className="size-16 rounded-lg border border-line-hairline object-cover"
                          src={m.imageUrl}
                        />
                      </button>
                    )}
                    {m.text && <p className="whitespace-pre-wrap break-words">{m.text}</p>}
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-start gap-2" key={m.id}>
                  <div
                    className={cn(
                      'max-w-[85%] rounded-2xl rounded-bl-sm border px-3 py-1.5',
                      m.tone === 'error'
                        ? 'border-danger-line bg-danger-bg text-danger-fg'
                        : 'border-line-hairline bg-surface-card text-body'
                    )}
                  >
                    {m.text}
                  </div>
                  {m.tone === 'error' && session.lastRequest && (
                    <button
                      aria-label={t.retryLast}
                      className={cn(BTN_ICON, 'shrink-0 text-danger-fg')}
                      onClick={session.retry}
                      title={t.retryLast}
                      type="button"
                    >
                      <ArrowBackUp />
                    </button>
                  )}
                </div>
              )
            )}
          </div>

          {session.draft && (
            <div className="flex items-center gap-2 border-t border-line-hairline px-4 py-2">
              <button
                className={cn(BTN_PRIMARY, 'h-7')}
                disabled={session.busy || !session.draft.previewUrl}
                onClick={() => void session.confirm()}
                type="button"
              >
                {session.busy ? t.processing : t.confirmAction}
              </button>
              <button className={BTN_GHOST} disabled={session.busy} onClick={() => setDesigning(false)} type="button">
                {t.discard}
              </button>
              <span className={cn(HINT_TEXT, 'ml-auto min-w-0 truncate')}>{t.confirmHint}</span>
            </div>
          )}

          {session.refImage && !session.draft && (
            <div className="flex items-center gap-2 border-t border-line-hairline px-4 py-1.5 text-[11px] text-body">
              <button
                aria-label={t.zoomRefImage}
                className="block cursor-zoom-in"
                onClick={() => setZoomUrl(session.refImage?.previewUrl ?? null)}
                type="button"
              >
                <img
                  alt={t.refImageAlt}
                  className="size-8 rounded border border-line-hairline object-cover"
                  src={session.refImage.previewUrl}
                />
              </button>
              {t.refImageAttached}
              <button className="text-muted transition hover:text-strong" onClick={session.clearRefImage} type="button">
                {common.remove}
              </button>
            </div>
          )}

          {session.draft && (
            <div className="border-t border-line-hairline px-4 py-2">
              <GenerationActionsGroup
                dense
                editDisabled={session.busy || !text.trim()}
                editReason={!text.trim() ? dict.generationActions.editRequiresFeedback : undefined}
                onEdit={() => sendRevise('edit')}
                onRegenerate={() => sendRevise('regenerate')}
                regenerateDisabled={session.busy}
              />
            </div>
          )}

          <div className="flex items-end gap-2 border-t border-line-hairline p-3">
            <textarea
              className={cn(INPUT_CLASS, 'min-h-[38px] flex-1 resize-none')}
              disabled={session.busy}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()

                  // 草稿态 Enter 默认微调；整体重绘走显式按钮。
                  if (session.draft) {
                    sendRevise('edit')
                  } else {
                    sendCreation()
                  }
                }
              }}
              placeholder={session.draft ? t.placeholderRefining : t.placeholderInitial}
              ref={inputRef}
              rows={2}
              value={text}
            />
            {!session.draft && (
              <button
                aria-label={t.attachImage}
                className={cn(BTN_ICON, 'h-9 w-9 shrink-0 self-end')}
                disabled={session.busy}
                onClick={() => void session.attachRefImage()}
                title={t.attachImageTitle}
                type="button"
              >
                <FileImage />
              </button>
            )}
            <button
              aria-label={selfSourceDict.open}
              className={cn(BTN_ICON, 'h-9 w-9 shrink-0 self-end')}
              disabled={session.busy}
              onClick={() => {
                void hydrateAvatarSeeds().finally(() => setOutfitSelfSourceOpen(true))
              }}
              title={selfSourceDict.openTitle}
              type="button"
            >
              <ImagePlus />
            </button>
            {!session.draft && (
              <button
                aria-label={t.send}
                className={cn(BTN_PRIMARY, 'h-9 w-9 shrink-0 self-end px-0')}
                disabled={session.busy || (!text.trim() && !session.refImage)}
                onClick={sendCreation}
                type="button"
              >
                <Send className="size-4" />
              </button>
            )}
          </div>
        </div>
      )}

      {zoomUrl && <PortraitLightbox name={t.imageAlt} onClose={() => setZoomUrl(null)} url={zoomUrl} />}

      <SelfSourceImageFlow
        adopt={adoptOutfitSelfSource}
        fetchPrompt={fetchOutfitSelfSourcePrompt}
        onClose={() => setOutfitSelfSourceOpen(false)}
        onUseAi={() => {
          setOutfitSelfSourceOpen(false)

          if (session.draft) {
            sendRevise('regenerate')

            return
          }

          // 无可发送内容时会静默拒绝——聚焦输入框把用户带回设计流程，避免点按钮毫无反馈。
          if (!text.trim() && !session.refImage) {
            inputRef.current?.focus()

            return
          }

          sendCreation()
        }}
        open={outfitSelfSourceOpen}
        referenceImages={outfitSelfSourceReferences}
        title={session.draft ? dict.generationActions.regenerate : t.startAction}
      />
    </div>
  )
}
