import { useState } from 'react'

import type { OnboardingAnswers } from '@/modules/character'
import { FullbodyReferencePanel, SelfSourceImageFlow } from '@/modules/character'
import { FolderOpen } from '@/shared/lib/icons'
import { BTN_SUBTLE, CreationActionBar, CreationFeedback, CreationWorkspace } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import type { Phase } from './back-transition'
import { EditAvatarButton, ONBOARDING_PRIMARY_BUTTON, PortraitPanel, RegenFeedbackInput } from './onboarding-components'
import type { OnboardingPortrait } from './use-onboarding-portrait'

interface PortraitPagesProps {
  phase: Phase
  answers: OnboardingAnswers
  hint: string | null
  setHint: (hint: string | null) => void
  setPhase: (phase: Phase) => void
  onBack: () => void
  portrait: OnboardingPortrait
}

export function PortraitPages({
  phase,
  answers,
  hint,
  setHint,
  setPhase,
  onBack,
  portrait
}: PortraitPagesProps): React.JSX.Element | null {
  const dict = useStrings()
  const [readyPreviewUrl, setReadyPreviewUrl] = useState<string | null>(null)

  const {
    activeAvatarId,
    sealingPortrait,
    portraitUrl,
    portraitPreviewId,
    portraitPanelHint,
    setPortraitPanelHint,
    avatarSelfSourceOpen,
    setAvatarSelfSourceOpen,
    refImage,
    presentationRef,
    setPresentationRef,
    updateRefImage,
    avatarBusy,
    portraitResultUnknown,
    portraitPreviewUnavailable,
    startAiHatching,
    currentHistoryItems,
    onSelectHistoryEntry,
    pickReferenceImage,
    pickPresentationImage,
    fetchAvatarPrompt,
    adoptAvatarSeed,
    sealPortrait,
    continueCurrentPortrait,
    confirmFullbody,
    regenerateAvatarPortrait,
    editAvatarPortrait,
    reloadAvatarPortrait,
    portraitSelectedIdx
  } = portrait

  if (phase === 'fullbody-reference' && activeAvatarId != null) {
    return (
      <FullbodyReferencePanel
        avatarId={activeAvatarId}
        key={activeAvatarId}
        onBack={onBack}
        onContinue={confirmFullbody}
      />
    )
  }

  if (phase !== 'portrait-generate' && phase !== 'portrait-avatar' && phase !== 'hatching') {
    return null
  }

  const generationBlocked = avatarBusy || sealingPortrait || portraitResultUnknown || portraitPreviewUnavailable

  const creationFeedback = (phase === 'hatching' || phase === 'portrait-avatar' || sealingPortrait) && (
    <CreationFeedback
      actions={
        portraitPanelHint ? (
          <button
            className={BTN_SUBTLE}
            disabled={avatarBusy || sealingPortrait}
            onClick={() => void reloadAvatarPortrait()}
            type="button"
          >
            {dict.creation.retryLoad}
          </button>
        ) : undefined
      }
      description={
        avatarBusy || sealingPortrait
          ? `${dict.creation.inputLocked} ${portraitUrl ? dict.creation.preserveCurrent : ''}`
          : portraitPanelHint || '预览并确认您的头像'
      }
      state={
        phase === 'hatching' || avatarBusy || sealingPortrait
          ? 'working'
          : portraitPanelHint
            ? portraitResultUnknown
              ? 'unknown'
              : 'error'
            : 'review'
      }
      title={
        sealingPortrait
          ? dict.common.saving
          : phase === 'hatching'
            ? hint || '正在生成头像…'
            : avatarBusy
              ? dict.creation.working
              : portraitPanelHint
                ? portraitResultUnknown
                  ? dict.creation.unknown
                  : '头像操作未能完成'
                : dict.creation.review
      }
    />
  )

  return (
    <>
      <CreationWorkspace
        footer={
          phase === 'portrait-generate' ? (
            <CreationActionBar className="mt-4" description={creationFeedback}>
              <button
                className="text-body transition hover:text-strong disabled:opacity-40"
                disabled={sealingPortrait}
                onClick={onBack}
                type="button"
              >
                上一步
              </button>
              <button
                className={ONBOARDING_PRIMARY_BUTTON}
                disabled={sealingPortrait}
                onClick={() => void startAiHatching()}
                type="button"
              >
                为伙伴画头像
              </button>
            </CreationActionBar>
          ) : phase === 'portrait-avatar' ? (
            <CreationActionBar className="mt-4" description={creationFeedback}>
              <div className="mt-3 flex items-center justify-between text-xs">
                <div className="flex flex-wrap gap-3">
                  <button
                    className="text-body transition hover:text-strong disabled:opacity-40"
                    disabled={generationBlocked}
                    onClick={() => {
                      setPortraitPanelHint(null)
                      void regenerateAvatarPortrait()
                    }}
                    type="button"
                  >
                    重新生成
                  </button>
                  <EditAvatarButton
                    busy={generationBlocked}
                    disabledByReference={Boolean(refImage || presentationRef)}
                    onEdit={() => {
                      setPortraitPanelHint(null)
                      void editAvatarPortrait()
                    }}
                  />
                  <button
                    className="text-body transition hover:text-strong disabled:opacity-40"
                    disabled={avatarBusy || sealingPortrait}
                    onClick={() => void reloadAvatarPortrait()}
                    type="button"
                  >
                    重新加载
                  </button>
                </div>
              </div>
              <button
                className="text-body transition hover:text-strong disabled:opacity-40"
                disabled={avatarBusy || sealingPortrait}
                onClick={onBack}
                type="button"
              >
                上一步
              </button>
              <button
                className={ONBOARDING_PRIMARY_BUTTON}
                disabled={
                  !portraitUrl ||
                  readyPreviewUrl !== portraitUrl ||
                  portraitPreviewId == null ||
                  portraitPreviewId !== activeAvatarId ||
                  avatarBusy ||
                  sealingPortrait ||
                  Boolean(portraitPanelHint)
                }
                onClick={() => void sealPortrait()}
                type="button"
              >
                满意，准备全身形象
              </button>
            </CreationActionBar>
          ) : (
            <CreationActionBar description={creationFeedback}>{null}</CreationActionBar>
          )
        }
      >
        <div className="space-y-4">
          {phase === 'portrait-generate' && (
            <div>
              <p className="text-[15px] font-medium text-strong">想象伙伴的样子</p>
              <p className="mt-1 text-xs text-body">
                描述喜欢的面容、发型或颜色，AI 会为伙伴画出头像。暂时没有想法也可以直接开始。
              </p>

              <div className="mt-3 space-y-1">
                <label className="text-xs text-body" htmlFor="onboarding-portrait-description">
                  头像描述（可选）
                </label>
                <RegenFeedbackInput id="onboarding-portrait-description" initial />
              </div>

              <details
                className="mt-3 rounded-xl border border-line-hairline bg-fill-trough p-3"
                open={refImage ? true : undefined}
              >
                <summary className="cursor-pointer text-xs text-body">添加参考图（可选）</summary>
                <p className="mt-1 text-[11px] text-muted">用于生成时借鉴外形，不会直接成为头像。</p>
                <div className="mt-2 flex items-center gap-2">
                  {refImage && (
                    <img alt="生成参考图" className="size-12 rounded-md object-contain" src={refImage.previewUrl} />
                  )}
                  <button className="text-xs text-strong" onClick={() => void pickReferenceImage()} type="button">
                    {refImage ? '更换参考图' : '添加参考图'}
                  </button>
                  {refImage && (
                    <button className="text-xs text-muted" onClick={() => updateRefImage(null)} type="button">
                      移除
                    </button>
                  )}
                </div>
                {hint && <p className="mt-2 text-xs text-strong">{hint}</p>}
              </details>

              {activeAvatarId != null && portraitUrl && (
                <div className="mt-3 flex items-center gap-3 rounded-xl border border-line-hairline p-3">
                  <img alt="之前制作的头像" className="size-12 rounded-lg object-contain" src={portraitUrl} />
                  <button
                    className="text-xs text-body transition hover:text-strong disabled:opacity-40"
                    disabled={avatarBusy || sealingPortrait}
                    onClick={() => void continueCurrentPortrait()}
                    type="button"
                  >
                    继续使用这个头像
                  </button>
                </div>
              )}
              {portraitPanelHint && (
                <p className="mt-3 text-xs text-danger-fg" role="alert">
                  {portraitPanelHint}
                </p>
              )}

              <button
                className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-strong disabled:opacity-40"
                disabled={avatarBusy || sealingPortrait}
                onClick={() => setAvatarSelfSourceOpen(true)}
                type="button"
              >
                <FolderOpen className="size-3.5" /> 使用自己的图片
              </button>
            </div>
          )}

          {(phase === 'portrait-avatar' || (phase === 'hatching' && portraitUrl)) && (
            <PortraitPanel
              avatarUrl={portraitUrl}
              hint={null}
              history={currentHistoryItems}
              introHint="预览并确认您的头像"
              name={answers.name?.trim() || '伙伴'}
              onPreviewReady={setReadyPreviewUrl}
              onSelectEntry={avatarBusy || sealingPortrait ? undefined : onSelectHistoryEntry}
              selectedIdx={portraitSelectedIdx}
            />
          )}

          {phase === 'portrait-avatar' && (
            <div className="mt-4">
              <>
                <details className="rounded-xl border border-line-hairline p-3" open={!portraitUrl ? true : undefined}>
                  <summary className="mb-3 cursor-pointer text-xs text-body">调整头像</summary>
                  <RegenFeedbackInput disabled={avatarBusy || sealingPortrait} />
                  <div className="mt-2 space-y-2 text-xs">
                    {refImage && (
                      <div className="flex items-center gap-2">
                        <img alt="形象参考图" className="h-9 w-9 rounded-md object-cover" src={refImage.previewUrl} />
                        <span className="text-[11px] text-muted">借鉴这张图的外形</span>
                        <button
                          className="ml-auto text-muted transition hover:text-strong"
                          disabled={avatarBusy || sealingPortrait}
                          onClick={() => updateRefImage(null)}
                          type="button"
                        >
                          移除
                        </button>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <button
                        className="rounded-full border border-dashed border-line-standard px-3 py-1 text-body transition hover:bg-fill-hover"
                        disabled={generationBlocked}
                        onClick={() => void (refImage ? pickPresentationImage() : pickReferenceImage())}
                        type="button"
                      >
                        {!refImage ? '＋ 形象参考图' : presentationRef ? '更换构图参考' : '＋ 光线与构图参考'}
                      </button>
                      {presentationRef && (
                        <>
                          <img
                            alt="光线与构图参考"
                            className="h-9 w-9 rounded-md object-cover"
                            src={presentationRef.previewUrl}
                          />
                          <span className="text-[10px] text-faint">参考光线与构图，沿用统一视觉风格</span>
                          <button
                            className="ml-auto text-muted transition hover:text-strong"
                            disabled={avatarBusy || sealingPortrait}
                            onClick={() => setPresentationRef(null)}
                            type="button"
                          >
                            移除
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </details>
              </>
            </div>
          )}
        </div>
      </CreationWorkspace>
      <SelfSourceImageFlow
        adopt={adoptAvatarSeed}
        fetchPrompt={fetchAvatarPrompt}
        hint="请使用单个角色、纯白背景的图片，采纳后将直接作为头像。"
        onClose={() => setAvatarSelfSourceOpen(false)}
        onUseAi={() => {
          setAvatarSelfSourceOpen(false)
          setHint(null)
          setPhase('portrait-generate')
        }}
        open={avatarSelfSourceOpen}
        referenceImages={refImage ? [{ label: '形象参考图', url: refImage.previewUrl }] : undefined}
        referenceRequired={Boolean(refImage)}
        title="使用自己的图片"
      />
    </>
  )
}
