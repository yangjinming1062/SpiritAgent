import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $videoGenError,
  $videoGenStage,
  $videoGenState,
  $videoPack,
  $videoPackStatus,
  generateVideoPack,
  hydrateVideoPack
} from '@/modules/character'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, ConfirmDialog, HINT_TEXT } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { OutfitSection } from './outfit-section'

const STAGE_TEXT_KEYS = {
  script: 'videoGenStageScript',
  submit: 'videoGenStageSubmit',
  generate: 'videoGenStageGenerate',
  download: 'videoGenStageDownload',
  process: 'videoGenStageProcess',
  publish: 'videoGenStagePublish'
} as const

// 外观页视频分区：承载外观参考（着装）管理，顶部是视频形象的生成入口与状态——
// 生成中按阶段提示，失败显示原因并可重试；就绪后显示激活包版本与动作数。
export function VideoSection(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const pack = useStore($videoPack)
  const status = useStore($videoPackStatus)
  const genState = useStore($videoGenState)
  const genStage = useStore($videoGenStage)
  const genError = useStore($videoGenError)
  const t = useStrings().living.appearance
  const [regenConfirmOpen, setRegenConfirmOpen] = useState(false)

  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateVideoPack()
    }
  }, [authKind])

  const stageText =
    genState === 'generating' ? (genStage != null ? t[STAGE_TEXT_KEYS[genStage]] : t.videoGenStageDefault) : null

  const statusLine =
    genState === 'failed'
      ? genError
      : (stageText ??
        (status === 'ready' && pack
          ? t.videoReady(pack.manifest.pack_version, pack.manifest.clips.length)
          : t.videoNotReady))

  const hasPack = status === 'ready' && pack != null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mx-4 mt-3 space-y-2 rounded-xl border border-line-hairline bg-surface-card px-3.5 py-2.5">
        <span className={cn('text-xs', genState === 'failed' ? 'text-danger-fg' : 'text-body')}>{statusLine}</span>
        <div className="flex gap-2">
          {genState !== 'generating' && !hasPack ? (
            <button
              className={BTN_PRIMARY}
              disabled={authKind !== 'authenticated'}
              onClick={() => void generateVideoPack()}
              type="button"
            >
              {t.videoGenAction}
            </button>
          ) : null}
          {genState !== 'generating' && hasPack ? (
            <button
              className={BTN_SUBTLE}
              disabled={authKind !== 'authenticated'}
              onClick={() => setRegenConfirmOpen(true)}
              type="button"
            >
              {t.videoRegenAction}
            </button>
          ) : null}
        </div>
        {genState === 'failed' ? <p className={HINT_TEXT}>{t.videoGenRetryHint}</p> : null}
        {genState !== 'generating' && !hasPack ? <p className={HINT_TEXT}>{t.videoGenHint}</p> : null}
      </div>
      <OutfitSection />
      <ConfirmDialog
        confirmLabel={t.videoRegenAction}
        description={t.videoRegenBody}
        onConfirm={() => void generateVideoPack({ force: true })}
        onOpenChange={setRegenConfirmOpen}
        open={regenConfirmOpen}
        title={t.videoRegenTitle}
      />
    </div>
  )
}
