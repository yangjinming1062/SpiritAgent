import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $actionCatalog,
  $outfits,
  $videoGenError,
  $videoGenStage,
  $videoGenState,
  $videoPacks,
  activateVideoPack,
  generateVideoPack,
  hydrateActionCatalog,
  hydrateVideoPack
} from '@/modules/character'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, ConfirmDialog, HINT_TEXT, INPUT_CLASS } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { OutfitSection } from './outfit-section'

const STAGE_TEXT_KEYS = {
  script: 'videoGenStageScript',
  pose: 'videoGenStagePose',
  submit: 'videoGenStageSubmit',
  generate: 'videoGenStageGenerate',
  download: 'videoGenStageDownload',
  process: 'videoGenStageProcess',
  publish: 'videoGenStagePublish'
} as const

function ActionPreview({ url }: { url: string }): React.JSX.Element {
  const [local, setLocal] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    setLocal(null)
    void window.spiritagent
      .apiAsset({ url, preferCache: true })
      .then(value => {
        if (!cancelled) {
          setLocal(value)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLocal(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [url])

  return (
    <video
      className="h-36 w-full object-contain"
      controls
      loop
      muted
      playsInline
      preload="metadata"
      src={local ?? undefined}
    />
  )
}

export function VideoSection(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const active = useStore($actionCatalog)
  const packs = useStore($videoPacks)
  const outfits = useStore($outfits)
  const initialVideoError = packs.length === 0 ? outfits.find(outfit => outfit.active)?.initialVideoError : null
  const genState = useStore($videoGenState)
  const genStage = useStore($videoGenStage)
  const genError = useStore($videoGenError)
  const t = useStrings().living.appearance
  const [regenConfirmOpen, setRegenConfirmOpen] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [redoAction, setRedoAction] = useState<string | null>(null)
  const [feedback, setFeedback] = useState('')
  const selected = packs.find(pack => pack.id === selectedId) ?? packs[0]
  const busy = genState === 'generating'

  const actionNames: Record<string, string> = {
    idle: t.videoIdle,
    walk_left: t.videoWalkLeft,
    walk_right: t.videoWalkRight,
    drag: t.videoDrag
  }

  // 动作网格消费任务行（系统槽位 + 动态动作）；
  // 动态动作优先显示名称（提案命名），系统槽位用固定文案。
  // 缺失的必需/可选系统槽位仍展示占位格，保留受控补齐入口。
  const gridActions: { key: string; label: string }[] = []
  const seenKeys = new Set<string>()

  for (const entry of selected?.actions ?? []) {
    if (seenKeys.has(entry.action)) {
      continue
    }

    seenKeys.add(entry.action)
    gridActions.push({
      key: entry.action,
      label: entry.name || actionNames[entry.action] || entry.action
    })
  }

  for (const slot of ['idle', 'drag', 'walk_left', 'walk_right'] as const) {
    if (!seenKeys.has(slot)) {
      seenKeys.add(slot)
      gridActions.push({ key: slot, label: actionNames[slot] })
    }
  }

  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateVideoPack()
      void hydrateActionCatalog()
    }
  }, [authKind])
  useEffect(() => {
    if (!busy) {
      return
    }

    const timer = window.setInterval(() => void hydrateVideoPack(true), 5000)

    return () => window.clearInterval(timer)
  }, [busy])

  const stageText = busy ? (genStage ? t[STAGE_TEXT_KEYS[genStage]] : t.videoGenStageDefault) : null

  const statusLine =
    genError ??
    stageText ??
    initialVideoError ??
    (active ? t.videoReady(active.catalogVersion, active.manifest.clips.length) : t.videoNotReady)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-4 mt-3 space-y-3 rounded-xl border border-line-hairline bg-surface-card px-3.5 py-2.5">
        <p className={cn('text-xs', genError || initialVideoError ? 'text-danger-fg' : 'text-body')}>{statusLine}</p>
        <p className={HINT_TEXT}>{t.videoGenHint}</p>
        <div className="flex flex-wrap gap-2">
          <button
            className={BTN_PRIMARY}
            disabled={busy || authKind !== 'authenticated'}
            onClick={() =>
              active
                ? setRegenConfirmOpen(true)
                : void generateVideoPack({ outfitId: selected?.outfit_id ?? undefined })
            }
            type="button"
          >
            {active ? t.videoRegenAction : t.videoGenAction}
          </button>
          {selected?.can_retry ? (
            <button
              className={BTN_SUBTLE}
              disabled={busy}
              onClick={() => void generateVideoPack({ retryPackId: selected.id })}
              type="button"
            >
              {t.videoResume}
            </button>
          ) : null}
          {selected?.status === 'ready' && !selected.active ? (
            <button
              className={BTN_SUBTLE}
              disabled={busy}
              onClick={() => void activateVideoPack(selected.id)}
              type="button"
            >
              {t.videoActivate}
            </button>
          ) : null}
        </div>
        {packs.length ? (
          <select
            aria-label={t.videoVersions}
            className={INPUT_CLASS}
            onChange={event => {
              setSelectedId(Number(event.target.value))
              setRedoAction(null)
            }}
            value={selected?.id}
          >
            {packs.map(pack => (
              <option key={pack.id} value={pack.id}>
                {t.videoVersion(pack.pack_version, pack.outfit_id ?? 0)}
                {pack.active ? ` · ${t.videoActive}` : ''}
              </option>
            ))}
          </select>
        ) : null}
        <div className="grid grid-cols-2 gap-3">
          {selected
            ? gridActions.map(({ key, label }) => {
                const clip = selected.actions.find(entry => entry.action === key)

                return (
                  <div className="space-y-2 rounded-lg border border-line-hairline p-2" key={key}>
                    <span className="text-xs text-strong">{label}</span>
                    {clip?.clip_url ? (
                      <ActionPreview url={clip.clip_url} />
                    ) : (
                      <p className={HINT_TEXT}>{clip?.error ?? t.videoActionOnDemand}</p>
                    )}
                    {clip?.motion_prompt ? <p className={HINT_TEXT}>{clip.motion_prompt}</p> : null}
                    {/* 系统槽位与动态动作均可单动作重做；动态重做保留动作身份，只替换素材。 */}
                    <button
                      className={BTN_SUBTLE}
                      disabled={busy || !selected.can_regenerate}
                      onClick={() => {
                        setRedoAction(key)
                        setFeedback('')
                      }}
                      type="button"
                    >
                      {clip ? t.videoRedoAction : t.videoGenMissingAction}
                    </button>
                  </div>
                )
              })
            : null}
        </div>
        {redoAction && selected ? (
          <div className="space-y-2">
            <label className={HINT_TEXT} htmlFor="video-action-feedback">
              {actionNames[redoAction]} · {t.videoFeedback}
            </label>
            <textarea
              className={INPUT_CLASS}
              id="video-action-feedback"
              maxLength={1000}
              onChange={event => setFeedback(event.target.value)}
              value={feedback}
            />
            <button
              className={BTN_PRIMARY}
              disabled={busy}
              onClick={() => {
                void generateVideoPack({ sourcePackId: selected.id, action: redoAction, feedback })
                setRedoAction(null)
                setSelectedId(null)
              }}
              type="button"
            >
              {t.videoRedoAction}
            </button>
          </div>
        ) : null}
      </div>
      <OutfitSection />
      <ConfirmDialog
        confirmLabel={t.videoRegenAction}
        description={t.videoRegenBody}
        onConfirm={() => void generateVideoPack({ force: true, outfitId: selected?.outfit_id ?? undefined })}
        onOpenChange={setRegenConfirmOpen}
        open={regenConfirmOpen}
        title={t.videoRegenTitle}
      />
    </div>
  )
}
