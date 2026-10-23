import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { AssetLibraryCard, AssetLibraryGrid } from '@/app/components/asset-library-card'
import { AssetLibraryHeader, type AssetLibrarySearch, AssetLibraryToolbar } from '@/app/components/asset-library-header'
import {
  $avatarSeeds,
  $outfitPolicy,
  $outfits,
  $wardrobeStatus,
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
import { ArrowBackUp, ArrowLeft, FileImage, ImageIcon, Pencil, Send, Trash2 } from '@/shared/lib/icons'
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
  CreationActionBar,
  CreationFeedback,
  CreationWorkspace,
  Disclosure,
  INPUT_CLASS,
  SettingCard,
  SettingsContent
} from '@/shared/panel'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { ImageReviseMode } from '@protocol'

import { $appearanceUi, $appearanceUiImportRevision, updateAppearanceUi } from './appearance-ui-state'

const CARD_ACTION_CLASS =
  'inline-flex h-6 items-center justify-center rounded-lg bg-fill-faint px-1.5 text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40'

// 衣柜是静态造型素材库：列表与详情只管理全身图与描述，不涉及穿着与呈现选择。
interface OutfitSectionProps {
  onSelectOutfit: (id: number) => void
  search: AssetLibrarySearch
}

export function OutfitSection({ onSelectOutfit, search }: OutfitSectionProps): React.JSX.Element {
  const outfits = useStore($outfits)
  const wardrobeStatus = useStore($wardrobeStatus)
  const outfitPolicy = useStore($outfitPolicy)
  const avatarSeeds = useStore($avatarSeeds)
  const dict = useStrings()
  const t = dict.living.outfit
  const common = dict.common
  const selfSourceDict = dict.selfSource
  const [busyId, setBusyId] = useState<number | null>(null)
  const [policyBusy, setPolicyBusy] = useState(false)
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [failedPreview, setFailedPreview] = useState<string | null>(null)
  const [loadedPreview, setLoadedPreview] = useState<string | null>(null)
  const ui = useStore($appearanceUi)
  const { designing, text, draftId } = ui
  const listScrollTop = useLatestRef(ui.listScrollTop ?? 0)
  const libraryRef = useRef<HTMLDivElement>(null)
  const visibleOutfits = outfits.filter(outfit => search.matches(outfit.name, outfit.description))
  const importRevision = useStore($appearanceUiImportRevision)
  const setDesigning = (next: boolean): void => updateAppearanceUi({ designing: next })
  const setText = (next: string): void => updateAppearanceUi({ text: next })
  const [outfitSelfSourceOpen, setOutfitSelfSourceOpen] = useState(false)
  const submittedText = useRef<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const begin = useAsyncGuard()

  useLayoutEffect(() => {
    const element = libraryRef.current

    if (element) {
      element.scrollTop = listScrollTop.current
    }

    // 滚动位置按帧落盘：连续滚动不再每个事件写一次账户缓存。
    let frame = 0

    const writePosition = (): void => {
      if (element) {
        updateAppearanceUi({ listScrollTop: element.scrollTop })
      }
    }

    const savePosition = (): void => {
      if (frame !== 0) {
        return
      }

      frame = window.requestAnimationFrame(() => {
        frame = 0
        writePosition()
      })
    }

    element?.addEventListener('scroll', savePosition)

    return () => {
      element?.removeEventListener('scroll', savePosition)

      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
        frame = 0
      }

      writePosition()
    }
  }, [designing, listScrollTop])

  // 确认入柜后重拉列表——新装转为参考图就绪。
  const session = useOutfitDesignSession(
    id => {
      updateAppearanceUi({
        designing: false,
        text: '',
        draftId: null,
        selectedOutfitId: id
      })
      void hydrateWardrobe()
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
        notifyError(err, t.deleteFailed)
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
      notify({ kind: 'error', message: t.policyFailed, title: t.policyFailed })
    }
  }

  const previewUrl = session.draft?.previewUrl ?? null
  const previewFailed = session.previewError || Boolean(previewUrl && failedPreview === previewUrl)
  const creationLocked = session.busy || session.resultUnknown || previewFailed
  const latestMessage = session.messages.at(-1)
  const hasInput = !!text.trim() || !!session.refImage

  const viewWardrobe = (): void => {
    void hydrateWardrobe()
    setDesigning(false)
  }

  // 首次生成（无草稿）：描述/参考图创建新设计。
  const sendCreation = (): void => {
    // 生成进行中会话内部会拒绝——此时不清空输入，避免丢字。
    if (creationLocked || !hasInput) {
      return
    }

    session.send(text, 'edit')
    submittedText.current = text
  }

  // 草稿反馈的两个显式操作（DESIGN「形象获取与确认」）：微调编辑上一版须带反馈，重新生成允许空反馈整体重绘。
  const sendRevise = (mode: ImageReviseMode): void => {
    if (creationLocked || (mode === 'edit' && !text.trim())) {
      return
    }

    session.send(text, mode)
    submittedText.current = text
  }

  // 自备图（着装立绘）：有草稿时提示词/采纳都走草稿重绘语境，否则走创建语境。
  const fetchOutfitSelfSourcePrompt = async (): Promise<string> => {
    const res = await requestResource('companion.post_outfit_prompt', {
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
      ? await requestResource('companion.post_outfit_regenerate', {
          path: { outfit_id: session.draft.id },
          body: { source: 'import', image: image.base64, content_type: image.contentType }
        })
      : await requestResource('companion.post_outfit', {
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

  const creationFeedback = (session.busy ||
    session.draft ||
    session.resultUnknown ||
    latestMessage?.tone === 'error') && (
    <CreationFeedback
      actions={
        session.resultUnknown || previewFailed ? (
          <button className={BTN_SUBTLE} onClick={viewWardrobe} type="button">
            {session.resultUnknown ? dict.creation.checkResult : dict.creation.retryLoad}
          </button>
        ) : undefined
      }
      description={
        session.resultUnknown
          ? t.design.timedOut
          : previewFailed
            ? t.design.previewFailed
            : latestMessage?.tone === 'error'
              ? latestMessage.text
              : session.busy
                ? `${dict.creation.inputLocked} ${dict.creation.preserveCurrent}`
                : t.confirmHint
      }
      state={
        session.busy
          ? 'working'
          : session.resultUnknown
            ? 'unknown'
            : previewFailed || latestMessage?.tone === 'error'
              ? 'error'
              : 'review'
      }
      title={
        session.busy
          ? session.stage === 'confirming'
            ? session.draft
              ? undefined
              : common.saving
            : session.stage === 'loading'
              ? t.processing
              : t.generating
          : session.resultUnknown
            ? dict.creation.unknown
            : previewFailed
              ? dict.creation.imageLoadFailed
              : latestMessage?.tone === 'error'
                ? t.design.generateFailed
                : dict.creation.review
      }
    />
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col border-b border-line-hairline bg-surface-panel">
      {designing ? (
        <CreationWorkspace
          footer={
            <CreationActionBar className="shrink-0 px-4" description={creationFeedback}>
              {session.draft ? (
                <>
                  <GenerationActionsGroup
                    dense
                    editDisabled={creationLocked || !text.trim()}
                    editReason={!text.trim() ? dict.generationActions.editRequiresFeedback : undefined}
                    onEdit={() => sendRevise('edit')}
                    onRegenerate={() => sendRevise('regenerate')}
                    regenerateDisabled={creationLocked}
                  />
                  <button
                    aria-atomic="true"
                    aria-live="polite"
                    className={BTN_PRIMARY}
                    disabled={creationLocked || !previewUrl || loadedPreview !== previewUrl}
                    onClick={() => void session.confirm()}
                    type="button"
                  >
                    {session.stage === 'confirming' ? common.saving : t.confirmAction}
                  </button>
                </>
              ) : (
                <button
                  className={BTN_PRIMARY}
                  disabled={creationLocked || !hasInput}
                  onClick={sendCreation}
                  type="button"
                >
                  <Send className="size-4" />
                  {t.send}
                </button>
              )}
              <button className={BTN_GHOST} onClick={viewWardrobe} type="button">
                {t.discard}
              </button>
            </CreationActionBar>
          }
          header={
            <>
              <header className="flex shrink-0 items-center gap-3 border-b border-line-hairline px-4 py-2.5">
                <button
                  aria-label={t.backToWardrobe}
                  className={cn(BTN_GHOST, 'h-8 shrink-0 px-2')}
                  onClick={viewWardrobe}
                  type="button"
                >
                  <ArrowLeft className="size-4" />
                  {t.backToWardrobe}
                </button>
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-strong" id="appearance-outfit-design-heading">
                    {t.startAction}
                  </h2>
                  <p className="truncate text-[11px] text-muted">
                    {session.draft ? t.design.resumeReady : t.designIntro}
                  </p>
                </div>
              </header>
            </>
          }
        >
          <div className="grid min-h-[360px] grid-cols-1 gap-3 p-4 md:h-full md:grid-cols-[minmax(0,1fr)_minmax(280px,0.72fr)]">
            <div className="relative min-h-64 min-w-0 overflow-hidden rounded-xl border border-line-hairline bg-fill-trough md:min-h-0">
              {previewUrl ? (
                <button
                  className="absolute inset-0 block h-full w-full cursor-zoom-in"
                  onClick={() => setZoomUrl(previewUrl)}
                  type="button"
                >
                  <img
                    alt={t.imageAlt}
                    className="block h-full w-full object-contain"
                    onError={() => setFailedPreview(previewUrl)}
                    onLoad={() => {
                      setLoadedPreview(previewUrl)
                      setFailedPreview(current => (current === previewUrl ? null : current))
                    }}
                    src={previewUrl}
                  />
                </button>
              ) : (
                <div className="absolute inset-0 grid place-items-center px-4 text-center">
                  <div>
                    <div className="text-xs text-body">{t.previewPlaceholderDesigning}</div>
                    <div className="mt-1 text-[10px] text-faint">{t.previewHint}</div>
                  </div>
                </div>
              )}
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
                      {m.tone === 'error' && session.lastRequest && !session.resultUnknown && (
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
                    disabled={session.busy}
                    onClick={session.clearRefImage}
                    type="button"
                  >
                    {common.remove}
                  </button>
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
                  disabled={creationLocked}
                  onClick={() => {
                    void hydrateAvatarSeeds().finally(() => setOutfitSelfSourceOpen(true))
                  }}
                  title={selfSourceDict.openTitle}
                  type="button"
                >
                  {selfSourceDict.open}
                </button>
              </div>
            </div>
          </div>
        </CreationWorkspace>
      ) : (
        <SettingsContent scrollRef={libraryRef}>
          <div className="space-y-4">
            {(session.busy || session.resultUnknown) && (
              <CreationFeedback
                actions={
                  <button className={BTN_SUBTLE} onClick={() => setDesigning(true)} type="button">
                    {t.actions.continueDesign}
                  </button>
                }
                description={session.resultUnknown ? t.design.timedOut : dict.creation.preserveCurrent}
                state={session.resultUnknown ? 'unknown' : 'working'}
                title={session.resultUnknown ? dict.creation.unknown : t.generating}
              />
            )}
            <AssetLibraryHeader
              policies={[
                {
                  id: 'outfit-autonomy',
                  label: t.policyLabel,
                  description: t.policyDesc,
                  status: policyBusy
                    ? common.saving
                    : outfitPolicy === 'locked'
                      ? t.policyStatusLocked
                      : t.policyStatusUnlocked,
                  checked: outfitPolicy !== 'locked',
                  disabled: policyBusy,
                  ariaLabel: t.policyToggleAria,
                  onChange: () => void togglePolicy()
                }
              ]}
            >
              <AssetLibraryToolbar
                createDisabled={session.busy}
                createLabel={t.startAction}
                onCreate={startDesign}
                onQueryChange={search.onQueryChange}
                query={search.query}
                searchLabel={t.search}
              />
            </AssetLibraryHeader>

            <AssetLibraryGrid>
              {visibleOutfits.map(outfit => {
                const statusLabel = t.statusLabels[outfit.status] ?? ''
                const canEdit = outfit.status === 'draft'

                const continueDesign = (): void => {
                  if (session.busy) {
                    return
                  }

                  updateAppearanceUi({ draftId: outfit.id })
                  session.adoptDraft(outfit.id, outfit.fullbodyUrl ?? '')
                  setDesigning(true)
                }

                const openOutfit = (): void => {
                  if (canEdit) {
                    continueDesign()
                  } else {
                    onSelectOutfit(outfit.id)
                  }
                }

                return (
                  <AssetLibraryCard
                    description={outfit.description || t.noDescription}
                    footer={
                      <>
                        <span className="min-w-0 flex-1 truncate text-[10px] text-faint">
                          {statusLabel || (outfit.status === 'ready' ? t.saved : '')}
                        </span>
                        <button
                          className={BTN_SUBTLE}
                          disabled={busyId === outfit.id}
                          onClick={openOutfit}
                          type="button"
                        >
                          {canEdit ? t.actions.continueDesign : t.openDetails}
                        </button>

                        <Disclosure
                          bodyClassName="flex flex-wrap gap-1"
                          className="w-full"
                          summaryClassName="text-[10px] text-muted"
                          title={dict.common.moreActions}
                          variant="inline"
                        >
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
                                disabled={session.busy}
                                onClick={continueDesign}
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
                            <button
                              aria-label={t.actions.delete}
                              className={cn(CARD_ACTION_CLASS, 'hover:text-danger-fg')}
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
                          </div>
                        </Disclosure>
                      </>
                    }
                    id={outfit.id}
                    key={outfit.id}
                    onOpen={openOutfit}
                    openLabel={`${t.openDetails}: ${outfit.name}`}
                    preview={
                      <>
                        {outfit.fullbodyUrl ? (
                          <img
                            alt={outfit.name}
                            className="absolute inset-0 h-full w-full object-contain"
                            loading="lazy"
                            src={outfit.fullbodyUrl}
                          />
                        ) : (
                          <div className="grid h-full place-items-center text-faint">
                            <ImageIcon className="size-5" />
                          </div>
                        )}
                      </>
                    }
                    previewRatio="portrait"
                    title={outfit.name}
                  />
                )
              })}
            </AssetLibraryGrid>
            {visibleOutfits.length === 0 ? (
              <SettingCard>
                <p className="p-6 text-center text-xs text-muted">
                  {wardrobeStatus === 'loading' || wardrobeStatus === 'idle'
                    ? t.loading
                    : search.searching
                      ? t.emptySearch
                      : t.empty}
                </p>
              </SettingCard>
            ) : null}
          </div>
        </SettingsContent>
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
