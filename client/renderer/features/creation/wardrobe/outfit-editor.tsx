import type React from 'react'

import { GenerationActionsGroup } from '@/features/character'
import { ArrowBackUp, ArrowLeft, FileImage, Send } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_ICON,
  BTN_PRIMARY,
  BTN_SUBTLE,
  CreationActionBar,
  CreationFeedback,
  CreationWorkspace,
  INPUT_CLASS
} from '@/ui'

import { OutfitUnknownActions } from './outfit-feedback'
import type { OutfitSectionModel } from './use-outfit-section'

/** 造型制作区：预览、会话消息、输入与确认动作；状态与命令由模型提供。 */
export function OutfitEditor({ model }: { model: OutfitSectionModel }): React.JSX.Element {
  const {
    session,
    t,
    common,
    dict,
    selfSourceDict,
    text,
    setText,
    creationLocked,
    hasInput,
    previewUrl,
    previewFailed,
    loadedPreview,
    setLoadedPreview,
    setFailedPreview,
    setZoomUrl,
    inputRef,
    messagesRef,
    sendCreation,
    sendRevise,
    viewWardrobe,
    setOutfitSelfSourceOpen,
    hydrateAvatarSeeds,
    latestMessage
  } = model

  const unknownActions = <OutfitUnknownActions model={model} />

  const creationFeedback = (session.busy ||
    session.draft ||
    session.resultUnknown ||
    latestMessage?.tone === 'error') && (
    <CreationFeedback
      actions={
        session.resultUnknown ? (
          unknownActions
        ) : previewFailed ? (
          <button className={BTN_SUBTLE} onClick={viewWardrobe} type="button">
            {dict.creation.retryLoad}
          </button>
        ) : undefined
      }
      description={
        session.resultUnknown
          ? session.inspected
            ? dict.creation.inspectionHint
            : t.design.timedOut
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
            <button className={BTN_PRIMARY} disabled={creationLocked || !hasInput} onClick={sendCreation} type="button">
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
              <p className="truncate text-[11px] text-muted">{session.draft ? t.design.resumeReady : t.designIntro}</p>
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
  )
}
