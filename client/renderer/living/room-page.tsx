import { useStore } from '@nanostores/react'
import type React from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { ArrowBackUp, Eye, FileImage, Loader2, RefreshCw, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, SettingCard, SettingRow, SettingsContent, Toggle } from '@/shared/panel'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import {
  $activeBackdrop,
  $backdropStatus,
  $roomHistory,
  $roomPolicy,
  regenerateRoom,
  rollbackRoom,
  setRoomPolicy
} from './room-backdrop-store'

// 不同于 shared/panel 的 SectionHeading——此处强调分组小标题（卡组上方），配色更弱。
function GroupHeading({ subtitle, title }: { subtitle?: string; title: string }): React.JSX.Element {
  return (
    <div className="flex items-baseline justify-between pb-1.5 pt-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">{title}</h3>
      {subtitle ? <span className="text-[10.5px] text-faint">{subtitle}</span> : null}
    </div>
  )
}

export function RoomPage(): React.JSX.Element {
  const history = useStore($roomHistory)
  const policy = useStore($roomPolicy)
  const status = useStore($backdropStatus)
  const activeBackdrop = useStore($activeBackdrop)
  const dict = useStrings()
  const t = dict.living.room
  const tToasts = dict.living.toasts
  const busy = status === 'pending'

  const handleChange = async (): Promise<void> => {
    if (busy) {
      return
    }

    triggerHaptic('open')
    await regenerateRoom()
  }

  const handleRollback = async (backdropId: string): Promise<void> => {
    if (busy) {
      return
    }

    triggerHaptic('tap')
    await rollbackRoom(backdropId)
  }

  const handleToggleLock = async (): Promise<void> => {
    triggerHaptic('selection')
    const next = policy === 'locked' ? 'llm_may_replace' : 'locked'
    await setRoomPolicy(next)
    notify({
      kind: 'info',
      message: next === 'locked' ? tToasts.roomLocked : tToasts.roomUnlocked
    })
  }

  return (
    <SettingsContent>
      <p className="mb-5 text-[11px] leading-relaxed text-faint">{t.intro}</p>

      {/* 当前房间展台卡片 */}
      <SettingCard>
        <div className="relative aspect-video w-full overflow-hidden bg-fill-muted/25">
          {activeBackdrop?.url ? (
            <img
              alt={activeBackdrop.brief || t.currentAltFallback}
              className="h-full w-full object-cover"
              src={activeBackdrop.url}
            />
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-faint">
              <FileImage className="size-8 opacity-30" />
              <span className="text-[11px]">{t.noBackdrop}</span>
            </div>
          )}

          {/* 生效中指示 */}
          {activeBackdrop?.url && !busy && status !== 'failed' ? (
            <div className="absolute top-2.5 left-2.5">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-0.5 text-[10.5px] font-medium text-white shadow-sm backdrop-blur-md">
                <span className="size-1.5 rounded-full bg-emerald-400" />
                {t.currentBadge}
              </span>
            </div>
          ) : null}

          {/* 生成中状态遮罩 */}
          {busy ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/60 p-4 text-center backdrop-blur-sm">
              <Loader2 className="size-6 animate-spin text-accent" />
              <p className="text-xs font-medium text-white">{t.pendingOverlay}</p>
              <p className="text-[10.5px] text-white/70">{t.pendingOverlayHint}</p>
            </div>
          ) : status === 'failed' ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/65 p-4 text-center backdrop-blur-sm">
              <p className="text-xs font-medium text-amber-300">{t.failedOverlay}</p>
              <p className="text-[10.5px] text-white/70">{t.failedOverlayHint}</p>
              <button
                className={cn(BTN_SUBTLE, 'mt-1 !h-7 px-3 text-xs')}
                onClick={() => void handleChange()}
                type="button"
              >
                <RefreshCw className="size-3" />
                {t.retryButton}
              </button>
            </div>
          ) : null}
        </div>

        {/* 房间描述与唯一的生成新房间主按钮 */}
        <div className="flex items-center justify-between gap-4 p-3.5">
          <div className="min-w-0 flex-1">
            <h4 className="truncate text-xs font-medium text-strong">
              {activeBackdrop?.brief || (busy ? t.briefPending : t.briefFallback)}
            </h4>
            <p className="mt-0.5 truncate text-[10.5px] text-faint">{busy ? t.subbriefPending : t.subbriefReady}</p>
          </div>

          <button
            className={cn(
              BTN_PRIMARY,
              'inline-flex shrink-0 items-center gap-1.5 px-3.5 text-xs font-medium transition active:scale-95'
            )}
            disabled={busy}
            onClick={() => void handleChange()}
            type="button"
          >
            {busy ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                <span>{t.generatingButton}</span>
              </>
            ) : (
              <>
                <Sparkles className="size-3.5" />
                <span>{t.generateButton}</span>
              </>
            )}
          </button>
        </div>
      </SettingCard>

      {/* 回滚历史 */}
      <GroupHeading subtitle={t.historySubtitle} title={t.historyTitle} />

      <SettingCard>
        {history.length === 0 ? (
          <p className="px-3.5 py-4 text-center text-[11px] text-faint">{t.historyEmpty}</p>
        ) : (
          <div className="grid grid-cols-5 gap-2 p-3.5">
            {history.map(entry => {
              const isCurrent = String(entry.id) === String(activeBackdrop?.id)

              return (
                <button
                  aria-label={isCurrent ? t.historyCurrentAria : t.historyRollbackAria(entry.id)}
                  className={cn(
                    'group relative aspect-video overflow-hidden rounded-lg border transition',
                    isCurrent
                      ? 'border-accent shadow-[0_0_8px_var(--ui-accent-soft,rgba(0,0,0,0.2))] ring-1 ring-accent'
                      : 'border-line-standard hover:border-accent'
                  )}
                  disabled={isCurrent || busy}
                  key={entry.id}
                  onClick={() => void handleRollback(entry.id)}
                  type="button"
                >
                  <img
                    alt={entry.brief || t.historyAltFallback}
                    className="h-full w-full object-cover transition duration-150 group-hover:scale-105"
                    src={entry.thumbnailUrl}
                  />

                  {isCurrent ? (
                    <span className="absolute bottom-1 left-1 rounded bg-accent/90 px-1 py-0.5 text-[9px] font-semibold text-inverse-fg backdrop-blur-sm">
                      {t.historyCurrentLabel}
                    </span>
                  ) : (
                    <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/60 opacity-0 transition group-hover:opacity-100">
                      <ArrowBackUp className="size-3.5 text-white" />
                      <span className="text-[9.5px] font-medium text-white/90">{t.historyRollbackLabel}</span>
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </SettingCard>

      {/* 房间政策 */}
      <GroupHeading title={t.policyTitle} />

      <SettingCard>
        <SettingRow
          description={t.policyDesc}
          label={
            <span className="flex items-center gap-1.5">
              <Eye className="size-3.5 text-accent" />
              <span>{t.policyLabel}</span>
            </span>
          }
        >
          <div className="flex items-center gap-2.5">
            <span className="text-[11px] text-faint">
              {policy === 'locked' ? t.policyStatusLocked : t.policyStatusUnlocked}
            </span>
            <Toggle
              ariaLabel={t.policyToggleAria}
              checked={policy !== 'locked'}
              onChange={() => void handleToggleLock()}
            />
          </div>
        </SettingRow>
      </SettingCard>
    </SettingsContent>
  )
}
