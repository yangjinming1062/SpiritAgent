import type { ReactNode } from 'react'

import { ActionSyncStatus } from '@/app/components/action-sync-status'
import type { DesktopVideoAction, DesktopVideoRefinementSource, DesktopVideoSet } from '@/modules/desktop-videos'
import { CreationActionBar, CreationWorkspace, Disclosure, INPUT_CLASS } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE } from './desktop-life-buttons'
import { DesktopLifePreview } from './desktop-life-preview'
import { DesktopLifeMetadata } from './desktop-life-resolution'
import {
  desktopLifeActionName,
  desktopLifeCreationStage,
  desktopLifeReadyForActions,
  isActionOutcomeUnknown,
  isActionPaused
} from './desktop-life-status'
import { DesktopLifeSteps } from './desktop-life-steps'
import styles from './desktop-life.module.css'

interface DesktopLifeActionViewProps {
  action: DesktopVideoAction
  set: DesktopVideoSet
  busy: boolean
  feedbackStatus: ReactNode
  issue: ReactNode
  noteDraft?: string
  onChangeNote: (note: string) => void
  onAddAction: () => void
  onCreateSet: () => void
  onDiscard: () => void
  onPause: () => void
  onUpload: () => void
  onRetry: () => void
  onContinueBase: () => void
  onMakeImage: (note: string, regenerate?: boolean) => void
  onMakeVideo: (note: string, videoFeedback?: string) => void
  onRefine: (source: DesktopVideoRefinementSource) => void
}

function previewUsesDraft(action: DesktopVideoAction): boolean {
  return !action.current && Boolean(action.draft?.image_confirmed && action.draft.image_url)
}

function hasPreviewMedia(action: DesktopVideoAction, draft: boolean): boolean {
  return Boolean(
    draft
      ? action.draft?.candidate_video_url || action.draft?.candidate_poster_url || action.draft?.image_url
      : action.current?.video_url || action.current?.poster_url
  )
}

function DesktopLifeFailure({ action: item }: { action: DesktopVideoAction }): React.JSX.Element | null {
  const strings = useStrings()
  const t = strings.desktopLife

  if (item.status !== 'failed' || isActionOutcomeUnknown(item)) {
    return null
  }

  return (
    <div className="space-y-2 text-sm text-body">
      <p>{t.failedAt(desktopLifeCreationStage(item, t))}</p>
      <p>
        {item.draft?.recovery_action === 'choose_activity'
          ? t.chooseActivityHint
          : item.draft?.recovery_action === 'replace_upload'
            ? t.replaceUploadHint
            : item.draft?.recovery_action === 'recreate_set'
              ? t.recreateSetHint
              : t.creationFailed}
      </p>
      {(item.draft?.error || item.error) && (
        <Disclosure bodyClassName="break-words" title={t.details} variant="inline">
          <p className="mt-2 break-words">{item.draft?.error || item.error}</p>
        </Disclosure>
      )}
    </div>
  )
}

export function DesktopLifeActionView({
  action: item,
  set,
  busy,
  feedbackStatus,
  issue,
  noteDraft,
  onChangeNote,
  onAddAction,
  onCreateSet,
  onDiscard,
  onPause,
  onUpload,
  onRetry,
  onContinueBase,
  onMakeImage,
  onMakeVideo,
  onRefine
}: DesktopLifeActionViewProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.desktopLife
  const sourceError = set.source_error
  const readyForActions = desktopLifeReadyForActions(set)

  const newBackgroundControl = (
    <button className={BTN_PRIMARY} disabled={busy} onClick={onCreateSet} type="button">
      {t.createSet}
    </button>
  )

  const unknown = isActionOutcomeUnknown(item)
  const chooseActivity = item.draft?.recovery_action === 'choose_activity'
  const pendingSource = item.key === 'idle' && set.can_upload_source
  const recreateSet = Boolean(sourceError || item.draft?.recovery_action === 'recreate_set')
  const processing = item.status === 'processing' && !isActionPaused(item) && !unknown

  const reusesBaseImage =
    item.key !== 'idle' &&
    (item.draft
      ? item.draft.continuity === 'base' && item.draft.image_confirmed && !item.draft.video_refinement
      : item.current?.continuity === 'base')

  const hideDraftImage = Boolean(reusesBaseImage && item.draft && !item.draft.candidate_video_url)

  const previewDraft = previewUsesDraft(item) && !hideDraftImage
  const showPreview = hasPreviewMedia(item, previewDraft)
  const hasCurrentPreview = hasPreviewMedia(item, false)

  const showingCurrentVideo = Boolean(item.current?.video_url && !previewDraft)

  const dependent = item.key !== 'idle' && (item.draft?.continuity ?? item.continuity) === 'base'

  const requiresBase = item.key !== 'idle' && !readyForActions && !sourceError && !processing && !unknown

  const awaitingFirstGeneration =
    item.definition_id !== null && ['not_generated', 'blocked'].includes(item.status) && !item.current && !item.draft

  const unavailableFirstGeneration = awaitingFirstGeneration && !item.can_generate
  const persistedNote = item.draft?.feedback ?? item.current?.feedback ?? ''

  const customizationReadOnly =
    unknown || isActionPaused(item) || chooseActivity || pendingSource || recreateSet || unavailableFirstGeneration

  const note = customizationReadOnly ? persistedNote : (noteDraft ?? persistedNote)
  const noteDirty = note !== persistedNote
  const videoNote = item.draft?.video_feedback ?? item.current?.video_feedback ?? ''
  const canRemakeVideo = Boolean(item.current || item.draft?.image_confirmed)

  const controls = sourceError ? (
    newBackgroundControl
  ) : unknown ? null : processing ? (
    item.draft && !['upload', 'image_upload'].includes(item.stage) ? (
      <button className={BTN_SUBTLE} disabled={busy} onClick={onPause} title={t.stopHint} type="button">
        {t.stopCreation}
      </button>
    ) : null
  ) : unavailableFirstGeneration ? null : chooseActivity ? (
    <button className={BTN_PRIMARY} disabled={busy} onClick={onAddAction} type="button">
      {t.chooseActivity}
    </button>
  ) : recreateSet ? (
    newBackgroundControl
  ) : pendingSource ? (
    <button className={BTN_PRIMARY} disabled={busy} onClick={onUpload} type="button">
      {set.source === 'image_upload' ? t.uploadImage : t.selectVideo}
    </button>
  ) : noteDirty ? (
    <button className={BTN_PRIMARY} disabled={busy} onClick={() => onMakeImage(note)} type="button">
      {t.applyCustomization}
    </button>
  ) : (
    <button
      className={BTN_PRIMARY}
      disabled={busy}
      onClick={() => {
        if (
          item.draft &&
          (item.draft.recovery_action === 'retry' || isActionPaused(item) || item.status === 'failed')
        ) {
          onRetry()
        } else {
          onMakeVideo(note)
        }
      }}
      type="button"
    >
      {isActionPaused(item)
        ? t.resume
        : item.draft?.recovery_action === 'retry' || item.status === 'failed'
          ? strings.common.retry
          : item.current && !item.needs_update
            ? t.regenerate
            : item.key === 'idle'
              ? t.createProduction
              : t.startAction}
    </button>
  )

  const content = (
    <section className={styles.detail} key={item.id}>
      {issue}
      {item.key === 'idle' && !item.current && <DesktopLifeSteps step={1} />}
      <ActionSyncStatus targets={item.sync_targets} />
      {sourceError ? (
        <p className="text-sm text-warning" role="alert">
          {sourceError}
        </p>
      ) : (
        <DesktopLifeFailure action={item} />
      )}
      {requiresBase ? (
        <div className="space-y-2">
          <h4 className="font-medium text-strong">{t.imageRequired}</h4>
          <p className="my-3 text-sm leading-relaxed text-body">{t.imageRequiredHint}</p>
        </div>
      ) : (
        <>
          {hideDraftImage && <p className="text-sm text-muted">{t.reusedBaseImageHint}</p>}
          {showPreview && (
            <>
              <p className="text-sm font-medium text-strong">{previewDraft ? t.candidateImage : t.currentProduct}</p>
              <DesktopLifePreview action={item} draft={previewDraft} set={set} />
            </>
          )}
          {<DesktopLifeMetadata set={set} />}
          {hasCurrentPreview && item.draft && <p className="text-sm text-muted">{t.savedCurrent}</p>}
          {hasCurrentPreview && previewDraft && (
            <Disclosure summaryClassName="text-sm font-medium text-strong" title={t.currentProduct} variant="bordered">
              <div className="mt-3">
                <DesktopLifePreview action={item} set={set} />
              </div>
            </Disclosure>
          )}
          {item.status !== 'failed' && !processing && !unknown && (item.error || item.draft?.error) && (
            <p className="text-sm text-warning" role="status">
              {t.unavailable}
            </p>
          )}
          {item.needs_update && <p className="text-sm text-warning">{t.needsUpdate}</p>}
          {!unknown && !processing && item.status === 'ready' && (
            <p className="text-sm text-muted" role="status">
              {item.key === 'idle' ? t.readyHint : t.actionReadyHint}
            </p>
          )}
          {!processing && (
            <Disclosure
              bodyClassName="mt-4 flex flex-col gap-3"
              className="rounded-xl border border-line-hairline p-4"
              open={Boolean(note.trim() || videoNote.trim() || noteDirty) || undefined}
              summaryClassName="text-sm font-medium text-strong"
              title={t.advanced}
              variant="bordered"
            >
              <div className="mt-4 flex flex-col gap-3">
                {showingCurrentVideo && (
                  <button
                    className={`${BTN_SUBTLE} self-start`}
                    disabled={
                      busy ||
                      recreateSet ||
                      (item.key !== 'idle' && !readyForActions) ||
                      item.status === 'processing' ||
                      item.draft?.submission_unknown
                    }
                    onClick={() => onRefine('current')}
                    type="button"
                  >
                    {t.refineVideo}
                  </button>
                )}
                {(note.trim() || noteDirty || (!item.draft?.video_refinement && !unknown)) && (
                  <>
                    <label className="flex flex-col gap-2 text-sm text-body">
                      {t.feedback}
                      <textarea
                        className={`${INPUT_CLASS} min-h-20 resize-y`}
                        disabled={busy || customizationReadOnly}
                        maxLength={1000}
                        onChange={event => onChangeNote(event.target.value)}
                        rows={2}
                        value={note}
                      />
                    </label>
                    {(note.trim() || noteDirty) && (
                      <div className="flex flex-wrap gap-2">
                        <button
                          className={BTN_GHOST}
                          disabled={busy || customizationReadOnly}
                          onClick={() => onChangeNote('')}
                          type="button"
                        >
                          {t.clearCustomization}
                        </button>
                      </div>
                    )}
                  </>
                )}
                {videoNote.trim() && (
                  <div className="flex flex-col gap-2 rounded-lg bg-accent/5 p-3">
                    <p className="text-sm font-medium text-strong">{t.savedVideoRequirements}</p>
                    <p className="whitespace-pre-wrap break-words text-sm text-body">{videoNote}</p>
                    {!customizationReadOnly && canRemakeVideo && (
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || noteDirty}
                        onClick={() => onMakeVideo(persistedNote, '')}
                        type="button"
                      >
                        {t.clearVideoRequirements}
                      </button>
                    )}
                  </div>
                )}
                {!dependent && (
                  <button
                    className={`${BTN_SUBTLE} self-start`}
                    disabled={
                      busy || unknown || chooseActivity || pendingSource || recreateSet || unavailableFirstGeneration
                    }
                    onClick={() => onMakeImage(note, true)}
                    type="button"
                  >
                    {t.regenerateImage}
                  </button>
                )}
                {item.draft && (
                  <button className={`${BTN_GHOST} self-start`} disabled={busy} onClick={onDiscard} type="button">
                    {t.discardDraft}
                  </button>
                )}
                <Disclosure
                  bodyClassName="text-xs text-muted"
                  className="text-xs text-muted"
                  title={t.details}
                  variant="inline"
                >
                  <p className="mt-2">{item.description}</p>
                  <p className="mt-2">
                    {item.kind === 'loop' ? t.loop : t.once} · {item.target_duration_seconds}s{' '}
                    {item.actual_duration_ms !== null && `· ${(item.actual_duration_ms / 1000).toFixed(1)}s`}
                  </p>
                  {item.use_when.length > 0 && (
                    <p className="mt-2">
                      {t.useWhen}：{item.use_when.join('；')}
                    </p>
                  )}
                  {item.avoid_when.length > 0 && (
                    <p className="mt-2">
                      {t.avoidWhen}：{item.avoid_when.join('；')}
                    </p>
                  )}
                </Disclosure>
              </div>
            </Disclosure>
          )}
        </>
      )}
    </section>
  )

  const footer = requiresBase ? (
    <CreationActionBar description={feedbackStatus}>
      <button className={BTN_PRIMARY} onClick={onContinueBase} type="button">
        {t.openImage}
      </button>
      {item.draft &&
        !chooseActivity &&
        !pendingSource &&
        !recreateSet &&
        (isActionPaused(item) || item.status === 'failed') && (
          <button className={BTN_SUBTLE} disabled={busy} onClick={onRetry} type="button">
            {isActionPaused(item) ? t.resume : strings.common.retry}
          </button>
        )}
      {item.draft && (
        <button className={BTN_GHOST} disabled={busy} onClick={onDiscard} type="button">
          {t.discardDraft}
        </button>
      )}
    </CreationActionBar>
  ) : controls ? (
    <CreationActionBar description={feedbackStatus}>{controls}</CreationActionBar>
  ) : feedbackStatus ? (
    <CreationActionBar description={feedbackStatus}>{null}</CreationActionBar>
  ) : null

  return (
    <CreationWorkspace
      className={styles.actionWorkspace}
      footer={footer ? <div className={styles.actionFooter}>{footer}</div> : undefined}
      header={
        <div className="flex items-center justify-between gap-2">
          <h3 className={styles.actionHeading}>{desktopLifeActionName(item, t)}</h3>
          {item.status === 'ready' && (
            <span className="text-xs text-muted" role="status">
              {t.ready}
            </span>
          )}
        </div>
      }
    >
      {content}
    </CreationWorkspace>
  )
}
