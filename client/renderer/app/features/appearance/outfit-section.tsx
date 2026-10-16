import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import {
  $avatarSeeds,
  $outfitPolicy,
  $outfits,
  $videoPacks,
  activateVideoPack,
  deleteOutfit,
  GenerationActionsGroup,
  hydrateAvatarSeeds,
  hydrateWardrobe,
  type PickedImage,
  resolvePortraitUrl,
  retryOutfitDescription,
  SelfSourceImageFlow,
  type SelfSourceReferenceImage,
  setOutfitPolicy,
  useOutfitDesignSession
} from '@/modules/character'
import { PortraitLightbox } from '@/shared'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { saveCreationDraftImage } from '@/shared/lib/creation-draft-image'
import { ArrowBackUp, ArrowLeft, FileImage, ImageIcon, Pencil, Plus, Send, Trash2 } from '@/shared/lib/icons'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_ICON,
  BTN_PRIMARY,
  BTN_SUBTLE,
  confirmAction,
  HINT_TEXT,
  INPUT_CLASS,
  Spinner,
  Toggle
} from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { ImageReviseMode } from '@protocol'

import { $appearanceUi, $appearanceUiImportRevision, updateAppearanceUi } from './appearance-ui-state'

// 列表卡 hover 操作钮：浮在立绘上，深色半透明底保证任何画面下可读。
const CARD_ACTION_CLASS =
  'inline-flex h-6 items-center justify-center rounded-lg bg-black/60 px-1.5 text-white/70 backdrop-blur-sm transition hover:bg-black/80 hover:text-white disabled:pointer-events-none disabled:opacity-40'

// 着装是衣柜页的首层资产；选择着装只切换预览与动作目录，穿着由明确操作完成。
interface OutfitSectionProps {
  onSelectOutfit: (id: number) => void
  onMaterialReady?: (id: number) => void
}

export function OutfitSection({ onSelectOutfit, onMaterialReady }: OutfitSectionProps): React.JSX.Element {
  const outfits = useStore($outfits)
  const outfitPolicy = useStore($outfitPolicy)
  const packs = useStore($videoPacks)
  const avatarSeeds = useStore($avatarSeeds)
  const dict = useStrings()
  const english = useStore($locale) === 'en'
  const t = dict.living.outfit
  const common = dict.common
  const selfSourceDict = dict.selfSource
  const [busyId, setBusyId] = useState<number | null>(null)
  const [policyBusy, setPolicyBusy] = useState(false)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const ui = useStore($appearanceUi)
  const { designing, text, draftId } = ui
  const listScrollTop = useLatestRef(ui.listScrollTop ?? 0)
  const libraryRef = useRef<HTMLElement>(null)
  const importRevision = useStore($appearanceUiImportRevision)
  const setDesigning = (next: boolean): void => updateAppearanceUi({ designing: next })
  const setText = (next: string): void => updateAppearanceUi({ text: next })
  const [outfitSelfSourceOpen, setOutfitSelfSourceOpen] = useState(false)
  const submittedText = useRef<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const begin = useAsyncGuard()
  const onMaterialReadyRef = useLatestRef(onMaterialReady)

  useLayoutEffect(() => {
    const element = libraryRef.current

    if (element) {
      element.scrollTop = listScrollTop.current
    }

    return () => {
      if (element) {
        updateAppearanceUi({ listScrollTop: element.scrollTop })
      }
    }
  }, [designing, listScrollTop])

  // 确认入柜后重拉列表——新装转为参考图就绪。
  const session = useOutfitDesignSession(
    id => {
      updateAppearanceUi({
        designing: false,
        text: '',
        draftId: null,
        selectedOutfitId: onMaterialReadyRef.current ? null : id
      })
      const confirmedUi = $appearanceUi.get()
      const importAtConfirmation = $appearanceUiImportRevision.get()
      const isLive = begin()
      void hydrateWardrobe().then(() => {
        if (
          isLive() &&
          $appearanceUi.get() === confirmedUi &&
          $appearanceUiImportRevision.get() === importAtConfirmation
        ) {
          onMaterialReadyRef.current?.(id)
        }
      })
    },
    id => updateAppearanceUi({ draftId: id })
  )

  const observedImportRevision = useRef(importRevision)
  const currentDraft = session.draft
  const adoptDraft = session.adoptDraft
  const resetSession = session.reset
  const restoreReference = session.restoreReference
  useEffect(() => {
    const imported = observedImportRevision.current !== importRevision
    observedImportRevision.current = importRevision

    if (imported) {
      submittedText.current = null
      setOutfitSelfSourceOpen(false)
      setZoomUrl(null)
      resetSession()
    }

    if (draftId === null) {
      if (!imported && currentDraft) {
        resetSession()
      }

      if (imported) {
        restoreReference()
      }

      return
    }

    const saved = outfits.find(outfit => outfit.id === draftId && outfit.status === 'draft')

    if (saved?.fullbodyUrl && (imported || currentDraft?.id !== saved.id)) {
      adoptDraft(saved.id, saved.fullbodyUrl)
    }
  }, [adoptDraft, currentDraft, draftId, importRevision, outfits, resetSession, restoreReference])

  useEffect(() => {
    messagesRef.current?.scrollTo?.({ top: messagesRef.current.scrollHeight })
  }, [session.messages])

  useEffect(() => {
    if (!session.busy && session.draft?.previewUrl && !session.lastRequest && submittedText.current !== null) {
      if ($appearanceUi.get().text === submittedText.current) {
        updateAppearanceUi({ text: '' })
      }

      submittedText.current = null
    }
  }, [session.busy, session.draft, session.lastRequest])

  const withBusy = (id: number, action: () => Promise<unknown>): void => {
    const epoch = currentClearEpoch()
    setBusyId(id)
    void action().finally(() => {
      if (epoch === currentClearEpoch()) {
        setBusyId(null)
      }
    })
  }

  const completeDescription = async (id: number): Promise<void> => {
    const epoch = currentClearEpoch()

    try {
      await retryOutfitDescription(id)
    } catch (err) {
      log.warn('outfit', 'retry description failed', err)

      if (epoch === currentClearEpoch()) {
        notifyError(err, t.descriptionRetryFailed)
      }
    }
  }

  const removeOutfit = async (id: number): Promise<void> => {
    const epoch = currentClearEpoch()

    try {
      await deleteOutfit(id)
    } catch (err) {
      log.warn('outfit', 'delete failed', err)

      if (epoch === currentClearEpoch()) {
        notify({ kind: 'error', message: backendDetailMessage(err, t.deleteFailed) })
      }
    }
  }

  const startDesign = (): void => {
    if (session.busy) {
      return
    }

    session.reset()
    updateAppearanceUi({ draftId: null, text: '' })
    void saveCreationDraftImage('outfit', null)
    setDesigning(true)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const togglePolicy = async (): Promise<void> => {
    setPolicyBusy(true)
    const epoch = currentClearEpoch()
    const next = outfitPolicy === 'locked' ? 'llm_may_replace' : 'locked'
    const saved = await setOutfitPolicy(next)
    setPolicyBusy(false)

    if (!saved && epoch === currentClearEpoch()) {
      notify({ kind: 'error', message: t.policyFailed })
    }
  }

  const previewUrl = session.draft?.previewUrl ?? null
  const hasInput = !!text.trim() || !!session.refImage

  // 首次生成（无草稿）：描述/参考图创建新设计。
  const sendCreation = (): void => {
    // 生成进行中会话内部会拒绝——此时不清空输入，避免丢字。
    if (session.busy || !hasInput) {
      return
    }

    session.send(text, 'edit')
    submittedText.current = text
  }

  // 草稿反馈的两个显式操作（DESIGN「形象获取与确认」）：微调编辑上一版须带反馈，重新生成允许空反馈整体重绘。
  const sendRevise = (mode: ImageReviseMode): void => {
    if (session.busy || (mode === 'edit' && !text.trim())) {
      return
    }

    session.send(text, mode)
    submittedText.current = text
  }

  // 自备图（着装立绘）：有草稿时提示词/采纳都走草稿重绘语境，否则走创建语境。
  const fetchOutfitSelfSourcePrompt = async (): Promise<string> => {
    const res = await requestResource('POST /api/companion/outfits/prompt', {
      body: session.draft
        ? { outfit_id: session.draft.id, feedback: text.trim() || undefined }
        : {
            description: text.trim() || undefined,
            image: session.refImage?.base64,
            content_type: session.refImage?.contentType
          }
    })

    return res.prompt
  }

  const adoptOutfitSelfSource = async (image: PickedImage): Promise<void> => {
    const isLive = begin()
    const importAtSubmit = $appearanceUiImportRevision.get()
    const selectedDraftId = $appearanceUi.get().draftId

    const res = session.draft
      ? await requestResource('POST /api/companion/outfits/{outfit_id}/image', {
          path: { outfit_id: session.draft.id },
          body: { source: 'import', image: image.base64, content_type: image.contentType }
        })
      : await requestResource('POST /api/companion/outfits', {
          body: {
            source: 'import',
            image: image.base64,
            content_type: image.contentType,
            description: text.trim() || undefined
          }
        })

    if (!isLive()) {
      return
    }

    void hydrateWardrobe()

    const resolved = res?.fullbody_url ? await resolvePortraitUrl(res.fullbody_url) : null

    if (
      !isLive() ||
      importAtSubmit !== $appearanceUiImportRevision.get() ||
      selectedDraftId !== $appearanceUi.get().draftId
    ) {
      return
    }

    // 从采纳后的草稿续上设计会话，可继续描述微调或直接确认入柜。
    if (res?.id) {
      updateAppearanceUi({ draftId: res.id })
      session.adoptDraft(res.id, resolved ?? '')
    }

    setText('')
  }

  const outfitSelfSourceReferences: SelfSourceReferenceImage[] | undefined = avatarSeeds.fullbodySeedUrl
    ? [{ label: selfSourceDict.refs.fullbodySeed, url: avatarSeeds.fullbodySeedUrl }]
    : undefined

  return (
    <div className="flex min-h-0 flex-1 flex-col border-b border-line-hairline bg-surface-panel">
      {designing ? (
        <section
          aria-labelledby="appearance-outfit-design-heading"
          className="flex min-h-0 flex-1 flex-col overflow-y-auto"
        >
          <header className="flex shrink-0 items-center gap-3 border-b border-line-hairline px-4 py-2.5">
            <button
              aria-label={dict.living.appearance.videoBackToOutfits}
              className={cn(BTN_GHOST, 'h-8 shrink-0 px-2')}
              disabled={session.busy}
              onClick={() => setDesigning(false)}
              type="button"
            >
              <ArrowLeft className="size-4" />
              {dict.living.appearance.videoBackToOutfits}
            </button>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-strong" id="appearance-outfit-design-heading">
                {t.startAction}
              </h2>
              <p className="truncate text-[11px] text-muted">{session.draft ? t.design.resumeReady : t.designIntro}</p>
            </div>
          </header>

          <div className="grid min-h-0 grid-cols-1 gap-3 p-4 md:flex-1 md:grid-cols-[minmax(0,1fr)_minmax(280px,0.72fr)] md:overflow-hidden">
            <div className="relative min-h-64 min-w-0 overflow-hidden rounded-xl border border-line-hairline bg-fill-trough md:min-h-0">
              {previewUrl ? (
                <button
                  className="absolute inset-0 block h-full w-full cursor-zoom-in"
                  onClick={() => setZoomUrl(previewUrl)}
                  type="button"
                >
                  <img alt={t.imageAlt} className="block h-full w-full object-contain" src={previewUrl} />
                </button>
              ) : (
                <div className="absolute inset-0 grid place-items-center px-4 text-center">
                  <div>
                    <div className="text-xs text-body">{t.previewPlaceholderDesigning}</div>
                    <div className="mt-1 text-[10px] text-faint">{t.previewHint}</div>
                  </div>
                </div>
              )}
              {session.busy ? (
                <div className="absolute inset-0 flex items-center justify-center gap-2 bg-black/40">
                  <Spinner className="size-5" />
                  <span className="text-xs text-white">{t.generating}</span>
                </div>
              ) : null}
            </div>

            <div className="flex min-h-80 min-w-0 flex-col overflow-hidden rounded-xl border border-line-hairline bg-surface-card md:min-h-0">
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
                <div className="flex min-w-0 flex-wrap items-center gap-2 border-t border-line-hairline px-4 py-2">
                  <button
                    className={cn(BTN_PRIMARY, 'h-7 shrink-0 whitespace-nowrap')}
                    disabled={session.busy || !session.draft.previewUrl}
                    onClick={() => void session.confirm()}
                    type="button"
                  >
                    {session.busy ? t.processing : t.confirmAction}
                  </button>
                  <button
                    className={cn(BTN_GHOST, 'shrink-0 whitespace-nowrap')}
                    disabled={session.busy}
                    onClick={() => setDesigning(false)}
                    type="button"
                  >
                    {t.discard}
                  </button>
                  <span className={cn(HINT_TEXT, 'min-w-0 flex-1 truncate')}>{t.confirmHint}</span>
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
                  <button
                    className="text-muted transition hover:text-strong"
                    onClick={session.clearRefImage}
                    type="button"
                  >
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

              <div className="flex flex-wrap items-end gap-2 border-t border-line-hairline p-3">
                <textarea
                  className={cn(INPUT_CLASS, 'min-h-[72px] w-full resize-none')}
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
                    className="inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-strong disabled:opacity-40"
                    disabled={session.busy}
                    onClick={() => void session.attachRefImage()}
                    title={t.attachImageTitle}
                    type="button"
                  >
                    <FileImage className="size-3.5" /> {t.attachImage}
                  </button>
                )}
                <button
                  aria-label={selfSourceDict.open}
                  className="inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-strong disabled:opacity-40"
                  disabled={session.busy}
                  onClick={() => {
                    void hydrateAvatarSeeds().finally(() => setOutfitSelfSourceOpen(true))
                  }}
                  title={selfSourceDict.openTitle}
                  type="button"
                >
                  {selfSourceDict.open}
                </button>
                {!session.draft && (
                  <button
                    aria-label={t.send}
                    className={cn(BTN_PRIMARY, 'ml-auto h-9 shrink-0')}
                    disabled={session.busy || !hasInput}
                    onClick={sendCreation}
                    type="button"
                  >
                    <Send className="size-4" />
                    {t.send}
                  </button>
                )}
              </div>
            </div>
          </div>
        </section>
      ) : (
        <section
          aria-labelledby="appearance-outfits-heading"
          className="@container flex min-h-0 flex-1 flex-col overflow-y-auto"
          onScroll={event => updateAppearanceUi({ listScrollTop: event.currentTarget.scrollTop })}
          ref={libraryRef}
        >
          <div className="mx-4 mt-3 flex items-center justify-between gap-4 rounded-xl border border-line-hairline p-4">
            <div className="min-w-0">
              <p className="text-sm font-medium text-strong">{t.policyLabel}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted">{t.policyDesc}</p>
              <p aria-live="polite" className="mt-1 text-xs text-muted">
                {policyBusy ? common.saving : outfitPolicy === 'locked' ? t.policyStatusLocked : t.policyStatusUnlocked}
              </p>
            </div>
            <Toggle
              ariaLabel={t.policyToggleAria}
              checked={outfitPolicy !== 'locked'}
              disabled={policyBusy}
              onChange={() => void togglePolicy()}
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 pt-3">
            <div className="flex min-w-0 items-baseline gap-2">
              <h2 className="text-sm font-semibold text-strong" id="appearance-outfits-heading">
                {t.wardrobeHeading}
              </h2>
              <span className="text-[11px] text-muted">{t.outfitCount(outfits.length)}</span>
            </div>
            <button className={BTN_PRIMARY} disabled={session.busy} onClick={startDesign} type="button">
              <Plus className="size-4" />
              {t.startAction}
            </button>
          </div>

          <div className="grid grid-cols-2 content-start items-start gap-3 px-4 py-4 @min-[640px]:grid-cols-3 @min-[920px]:grid-cols-4">
            {outfits.map(outfit => {
              const statusLabel = outfit.active ? t.wearing : (t.statusLabels[outfit.status] ?? '')
              const canEdit = outfit.status === 'draft'
              const canDelete = !outfit.active
              const pack = packs.find(item => item.outfit_id === outfit.id)
              const canWear = pack?.status === 'ready' && pack.identity_review !== 'review'

              const openOutfit = (): void => {
                if (canEdit) {
                  updateAppearanceUi({ draftId: outfit.id })
                  session.adoptDraft(outfit.id, outfit.fullbodyUrl ?? '')
                  setDesigning(true)
                } else {
                  onSelectOutfit(outfit.id)
                }
              }

              return (
                <article
                  className="group relative min-w-0 overflow-hidden rounded-xl border border-line-hairline bg-surface-card transition hover:border-line-strong"
                  key={outfit.id}
                >
                  <button className="block w-full text-left" onClick={openOutfit} type="button">
                    <div className="relative aspect-[4/5] overflow-hidden bg-fill-trough">
                      {outfit.fullbodyUrl ? (
                        <img
                          alt={outfit.name}
                          className="absolute inset-0 h-full w-full object-contain"
                          loading="lazy"
                          src={outfit.fullbodyUrl}
                        />
                      ) : null}
                      {statusLabel ? (
                        <span
                          className={cn(
                            'absolute bottom-1.5 left-1.5 max-w-[calc(100%-12px)] truncate rounded-md px-1.5 py-0.5 text-[10px] text-white',
                            outfit.active ? 'bg-emerald-600/90' : 'bg-black/70'
                          )}
                        >
                          {statusLabel}
                        </span>
                      ) : null}
                    </div>
                    <div className="px-2 py-1.5">
                      <p className="truncate text-[11px] font-medium text-strong">{outfit.name}</p>
                    </div>
                  </button>

                  <div className="px-2 pb-2">
                    <button
                      className={cn(BTN_SUBTLE, 'w-full justify-center')}
                      disabled={busyId === outfit.id || (outfit.active && !onMaterialReady)}
                      onClick={() => {
                        if (onMaterialReady && outfit.status === 'ready') {
                          onMaterialReady(outfit.id)
                        } else if (canWear && pack && !outfit.active) {
                          const epoch = currentClearEpoch()
                          withBusy(outfit.id, async () => {
                            if (!(await activateVideoPack(pack.id)) && epoch === currentClearEpoch()) {
                              onSelectOutfit(outfit.id)
                            }
                          })
                        } else {
                          openOutfit()
                        }
                      }}
                      type="button"
                    >
                      {onMaterialReady && outfit.status === 'ready'
                        ? english
                          ? 'Choose this outfit'
                          : '选这套衣服'
                        : outfit.active
                          ? t.wearing
                          : canEdit
                            ? t.actions.continueDesign
                            : canWear
                              ? dict.living.appearance.videoActivate
                              : pack?.identity_review === 'review'
                                ? english
                                  ? 'Review appearance'
                                  : '查看并确认'
                                : pack?.status === 'processing'
                                  ? english
                                    ? 'View progress'
                                    : '查看进度'
                                  : english
                                    ? 'Prepare to wear'
                                    : '准备穿上'}
                    </button>
                  </div>

                  <details className="border-t border-line-hairline px-2 py-2">
                    <summary className="cursor-pointer text-xs text-muted">{dict.common.moreActions}</summary>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {outfit.fullbodyUrl ? (
                        <button
                          aria-label={t.zoomOutfit(outfit.name)}
                          className={CARD_ACTION_CLASS}
                          onClick={() => setZoomUrl(outfit.fullbodyUrl)}
                          title={t.imageAlt}
                          type="button"
                        >
                          <ImageIcon className="size-3.5" />
                        </button>
                      ) : null}
                      {canEdit && (
                        <button
                          aria-label={t.actions.continueDesign}
                          className={CARD_ACTION_CLASS}
                          onClick={() => {
                            updateAppearanceUi({ draftId: outfit.id })
                            session.adoptDraft(outfit.id, outfit.fullbodyUrl ?? '')
                            setDesigning(true)
                          }}
                          title={t.actions.continueDesignTitle}
                          type="button"
                        >
                          <Pencil className="size-3.5" />
                        </button>
                      )}
                      {outfit.status === 'ready' && outfit.descriptionStatus === 'failed' && (
                        <button
                          aria-label={t.retryDescription}
                          className={CARD_ACTION_CLASS}
                          disabled={busyId === outfit.id}
                          onClick={() => withBusy(outfit.id, () => completeDescription(outfit.id))}
                          title={outfit.descriptionError ?? t.retryDescription}
                          type="button"
                        >
                          {t.retryDescription}
                        </button>
                      )}
                      {canDelete && (
                        <button
                          aria-label={t.actions.delete}
                          className={cn(CARD_ACTION_CLASS, 'hover:text-rose-300')}
                          disabled={busyId === outfit.id}
                          onClick={() => {
                            void confirmAction({
                              title: t.actions.deleteTitle,
                              description: outfit.name,
                              confirmLabel: t.actions.delete,
                              variant: 'destructive'
                            }).then(confirmed => {
                              if (confirmed) {
                                withBusy(outfit.id, () => removeOutfit(outfit.id))
                              }
                            })
                          }}
                          title={t.actions.deleteTitle}
                          type="button"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      )}
                    </div>
                  </details>
                </article>
              )
            })}

            {outfits.length === 0 ? <p className="self-center text-xs text-muted">{t.empty}</p> : null}
          </div>
        </section>
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
          if (!hasInput) {
            inputRef.current?.focus()

            return
          }

          sendCreation()
        }}
        open={outfitSelfSourceOpen}
        promptBlockedReason={!session.draft && !hasInput ? t.design.promptRequiresInput : undefined}
        referenceImages={outfitSelfSourceReferences}
        title={session.draft ? dict.generationActions.regenerate : t.startAction}
      />
    </div>
  )
}
