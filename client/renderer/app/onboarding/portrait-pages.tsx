import type { OnboardingAnswers } from '@/modules/character'
import { FullbodyReferencePanel, SelfSourceImageFlow } from '@/modules/character'
import { FolderOpen, Sparkles } from '@/shared/lib/icons'

import type { Phase } from './back-transition'
import { EditAvatarButton, PortraitPanel, RegenFeedbackInput, SpinnerWithText } from './onboarding-components'
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
}: PortraitPagesProps): React.JSX.Element {
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

  return (
    <>
      {phase === 'portrait-choose' && (
        <div>
          <p className="text-[15px] font-medium text-strong">选择头像获取方式</p>
          <p className="mt-1 text-xs text-body">
            先为伙伴确定头像，再准备全身形象。可以让 AI 绘制，也可以直接上传您准备好的图片。
          </p>

          <div className="mt-4 flex flex-col gap-3">
            <button
              className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
              disabled={sealingPortrait}
              onClick={() => setPhase('portrait-generate')}
              type="button"
            >
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[14px] font-medium text-strong">
                  <Sparkles className="size-4 text-muted" /> AI 生成
                </span>
                <span className="text-xs text-muted">AI 绘制 →</span>
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                可在生成前补充头像描述与参考图，之后预览、微调或重新生成。
              </p>
            </button>

            <button
              className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
              disabled={avatarBusy || sealingPortrait}
              onClick={() => setAvatarSelfSourceOpen(true)}
              type="button"
            >
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[14px] font-medium text-strong">
                  <FolderOpen className="size-4 text-muted" /> 直接上传
                </span>
                <span className="text-xs text-muted">选择图片 →</span>
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                上传一张您准备好的图片作为头像；也可以获取提示词，在外部绘制后上传。
              </p>
            </button>

            {activeAvatarId != null && portraitUrl && (
              <button
                className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
                disabled={avatarBusy || sealingPortrait}
                onClick={() => void continueCurrentPortrait()}
                type="button"
              >
                <div className="flex items-center justify-between">
                  <span className="text-[14px] font-medium text-strong">继续使用当前头像</span>
                  <span className="text-xs text-muted">已有草稿 →</span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                  沿用之前的头像草稿，直接进入全身形象准备。
                </p>
              </button>
            )}
          </div>

          {portraitPanelHint && <p className="mt-3 text-xs text-danger-fg">{portraitPanelHint}</p>}

          <div className="mt-4 flex items-center justify-between text-xs">
            <button
              className="text-body transition hover:text-strong disabled:opacity-30"
              disabled={sealingPortrait}
              onClick={onBack}
              type="button"
            >
              上一步
            </button>
          </div>
        </div>
      )}

      {phase === 'portrait-generate' && (
        <div>
          <p className="text-[15px] font-medium text-strong">用 AI 生成头像</p>
          <p className="mt-1 text-xs text-body">
            可以描述希望头像呈现的面容、发型、颜色和辨识细节，也可以附加参考图。留空即可按角色设定生成。
          </p>

          <div className="mt-3 space-y-1">
            <label className="text-xs text-body" htmlFor="onboarding-portrait-description">
              头像描述（可选）
            </label>
            <RegenFeedbackInput id="onboarding-portrait-description" initial />
          </div>

          <div className="mt-3 rounded-xl border border-line-hairline bg-fill-trough p-3">
            <p className="text-xs text-body">参考图（可选）</p>
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
          </div>

          <div className="mt-4 flex items-center justify-between text-xs">
            <button
              className="text-body transition hover:text-strong disabled:opacity-40"
              disabled={sealingPortrait}
              onClick={() => {
                setHint(null)
                setPhase('portrait-choose')
              }}
              type="button"
            >
              上一步
            </button>
            <button
              className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
              disabled={sealingPortrait}
              onClick={() => void startAiHatching()}
              type="button"
            >
              开始生成
            </button>
          </div>
        </div>
      )}

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
        title="直接上传"
      />

      {phase === 'hatching' && <SpinnerWithText size="h-6 w-6" text={hint || '正在生成头像…'} />}

      {phase === 'portrait-avatar' && (
        <PortraitPanel
          avatarUrl={portraitUrl}
          hint={portraitPanelHint}
          history={currentHistoryItems}
          introHint="预览并确认您的头像"
          name={answers.name?.trim() || '伙伴'}
          onSelectEntry={onSelectHistoryEntry}
          selectedIdx={portraitSelectedIdx}
        />
      )}

      {phase === 'portrait-avatar' && (
        <div className="mt-4">
          {avatarBusy ? (
            <SpinnerWithText text="正在重新生成头像…" />
          ) : (
            <>
              <RegenFeedbackInput />
              <div className="mt-2 space-y-2 text-xs">
                {refImage && (
                  <div className="flex items-center gap-2">
                    <img alt="形象参考图" className="h-9 w-9 rounded-md object-cover" src={refImage.previewUrl} />
                    <span className="text-[10px] text-faint">形象参考图（每次重生都会携带）</span>
                    <button
                      className="ml-auto text-muted transition hover:text-strong"
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
                        onClick={() => setPresentationRef(null)}
                        type="button"
                      >
                        移除
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="mt-3 flex items-center justify-between text-xs">
                <div className="flex flex-wrap gap-3">
                  <button
                    className="text-body transition hover:text-strong disabled:opacity-40"
                    disabled={sealingPortrait}
                    onClick={onBack}
                    type="button"
                  >
                    上一步
                  </button>
                  <button
                    className="text-body transition hover:text-strong disabled:opacity-40"
                    disabled={avatarBusy || sealingPortrait}
                    onClick={() => {
                      setPortraitPanelHint(null)
                      void regenerateAvatarPortrait()
                    }}
                    type="button"
                  >
                    重新生成
                  </button>
                  <EditAvatarButton
                    busy={avatarBusy || sealingPortrait}
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
                <button
                  className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                  disabled={
                    !portraitUrl || portraitPreviewId == null || portraitPreviewId !== activeAvatarId || sealingPortrait
                  }
                  onClick={() => void sealPortrait()}
                  type="button"
                >
                  确认
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {phase === 'fullbody-reference' && activeAvatarId != null && (
        <FullbodyReferencePanel
          avatarId={activeAvatarId}
          key={activeAvatarId}
          onBack={onBack}
          onContinue={confirmFullbody}
        />
      )}
    </>
  )
}
