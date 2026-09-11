import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { AssetPackPreview } from '@/2d'
import {
  $outfitPolicy,
  $outfits,
  activateOutfit,
  deleteOutfit,
  hydrateWardrobe,
  setOutfitPolicy,
  useOutfitDesignSession
} from '@/companion'
import { PortraitLightbox } from '@/shared'
import { Check, FileImage, Pencil, Send, Trash2 } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_ICON, BTN_PRIMARY, HINT_TEXT, INPUT_CLASS, Spinner, Toggle } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

// 列表卡 hover 操作钮：浮在立绘上，深色半透明底保证任何画面下可读。
const CARD_ACTION_CLASS =
  'inline-flex h-6 items-center justify-center rounded-lg bg-black/60 px-1.5 text-white/70 backdrop-blur-sm transition hover:bg-black/80 hover:text-white disabled:pointer-events-none disabled:opacity-40'

// 衣柜三栏页（DESIGN §8）：左侧外观列表（竖版大图卡），右上大图预览，右下类聊天的设计区。
// 设计流程不再弹窗——描述 / 参考图 / 微调反馈 / 确认入柜都在右半侧完成。
export function WardrobePage(): React.JSX.Element {
  const outfits = useStore($outfits)
  const outfitPolicy = useStore($outfitPolicy)
  const dict = useStrings()
  const t = dict.living.wardrobe
  const common = dict.common
  const [busyId, setBusyId] = useState<number | null>(null)
  const [policyBusy, setPolicyBusy] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [designing, setDesigning] = useState(false)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)

  // 确认入柜后重拉列表——新装进入切分态并自动穿上。
  const session = useOutfitDesignSession(() => {
    setDesigning(false)
    setText('')
    void hydrateWardrobe()
  })

  useEffect(() => {
    void hydrateWardrobe()
  }, [])

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

  const retrySplit = async (id: number): Promise<void> => {
    try {
      await window.spiritagent.api({ path: `/api/companion/outfits/${id}/confirm`, method: 'POST' })
      await hydrateWardrobe()
    } catch (err) {
      log.warn('wardrobe', 'retry split failed', err)
    }
  }

  const startDesign = (): void => {
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

  const sendText = (): void => {
    // 生成进行中会话内部会拒绝——此时不清空输入，避免丢字。
    if (session.busy || (!text.trim() && !session.refImage)) {
      return
    }

    session.send(text)
    setText('')
  }

  return (
    <div className="flex min-h-0 flex-1">
      {/* 左：外观列表（方形大图卡——统一方形取景框，任意比例立绘 contain 完整呈现，文字收缩到图下方两行） */}
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
        </div>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2.5 pt-4 pb-3">
          {outfits.length === 0 ? (
            <p className="px-1 pt-2 text-xs text-muted">{t.empty}</p>
          ) : (
            outfits.map(outfit => {
              const statusLabel = t.statusLabels[outfit.status] ?? ''
              const deletable = !outfit.active && outfit.status !== 'splitting'
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
                      {outfit.status === 'draft' && (
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
                            withBusy(outfit.id, () => retrySplit(outfit.id))
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

                    {outfit.status === 'splitting' && (
                      <div className="absolute inset-0 grid place-items-center bg-black/45">
                        <Spinner />
                      </div>
                    )}
                  </div>

                  <div className="px-2.5 py-2">
                    <p className="truncate text-[11px] font-medium text-strong">{outfit.name}</p>
                    <p className="mt-0.5 truncate text-[10px] text-muted">
                      {outfit.status === 'splitting' && outfit.pendingWear ? t.autoWearAfterSplit : outfit.description}
                    </p>
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* 右：上预览 / 下设计区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 固定方形取景框：全身图比例随物种而异，不假设方形——contain 完整
            呈现，长方图两侧留空。外层 inset 定位拿到确定的高宽（auto 高容器里
            百分比 max 解析不到，图会按内容自然高溢出可视区），内层 h-full +
            aspect-square 取可用区内最大正方形。 */}
        <div className="relative min-h-0 flex-1">
          {!designing && selected?.asset ? (
            <AssetPackPreview
              imageUrl={previewUrl ?? null}
              key={`${selected.id}:${selected.asset.content_hash ?? selected.asset.id}`}
              source={selected.asset}
            />
          ) : previewUrl ? (
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

        <div className={cn('flex shrink-0 flex-col border-t border-line-hairline', designing ? 'h-56' : 'h-28')}>
          {!designing ? (
            <div className="grid flex-1 place-items-center px-6 text-center">
              <div>
                <p className="text-xs text-body">{t.startPrompt}</p>
                <button className={cn(BTN_PRIMARY, 'mt-3')} onClick={startDesign} type="button">
                  {t.startAction}
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3 text-xs" ref={messagesRef}>
                {session.messages.length === 0 && <p className="text-faint">{t.designIntro}</p>}
                {session.messages.map(m =>
                  m.role === 'user' ? (
                    <div className="flex justify-end" key={m.id}>
                      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent/20 px-3 py-1.5 text-strong">
                        {m.imageUrl && (
                          <img
                            alt={t.refImageAlt}
                            className="mb-1 size-16 rounded-lg border border-line-hairline object-cover"
                            src={m.imageUrl}
                          />
                        )}
                        {m.text && <p className="whitespace-pre-wrap break-words">{m.text}</p>}
                      </div>
                    </div>
                  ) : (
                    <div className="flex justify-start" key={m.id}>
                      <div
                        className={cn(
                          'max-w-[85%] rounded-2xl rounded-bl-sm border px-3 py-1.5',
                          m.tone === 'error'
                            ? 'border-rose-400/25 bg-rose-500/10 text-rose-200'
                            : 'border-line-hairline bg-surface-card text-body'
                        )}
                      >
                        {m.text}
                      </div>
                    </div>
                  )
                )}
              </div>

              {session.draft && (
                <div className="flex items-center gap-2 border-t border-line-hairline px-4 py-2">
                  <button
                    className={cn(BTN_PRIMARY, 'h-7')}
                    disabled={session.busy}
                    onClick={() => void session.confirm()}
                    type="button"
                  >
                    {session.busy ? t.processing : t.confirmAndWear}
                  </button>
                  <button
                    className={BTN_GHOST}
                    disabled={session.busy}
                    onClick={() => setDesigning(false)}
                    type="button"
                  >
                    {t.discard}
                  </button>
                  <span className={cn(HINT_TEXT, 'ml-auto')}>{t.confirmHint}</span>
                </div>
              )}

              {session.refImage && !session.draft && (
                <div className="flex items-center gap-2 border-t border-line-hairline px-4 py-1.5 text-[11px] text-body">
                  <img
                    alt={t.refImageAlt}
                    className="size-8 rounded border border-line-hairline object-cover"
                    src={session.refImage.previewUrl}
                  />
                  {t.refImageAttached}
                  <button
                    className="text-muted transition hover:text-strong"
                    onClick={session.clearRefImage}
                    type="button"
                  >
                    {common.remove}
                  </button>
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
                      sendText()
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
                  aria-label={t.send}
                  className={cn(BTN_PRIMARY, 'h-9 w-9 shrink-0 self-end px-0')}
                  disabled={session.busy || (!text.trim() && !session.refImage)}
                  onClick={sendText}
                  type="button"
                >
                  <Send className="size-4" />
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {zoomUrl && <PortraitLightbox name={t.imageAlt} onClose={() => setZoomUrl(null)} url={zoomUrl} />}
    </div>
  )
}
