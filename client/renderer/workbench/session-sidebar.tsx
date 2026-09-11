import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionId } from '@/chat/chat-store'
import {
  $archivedLoading,
  $archivedSessions,
  $archiveOpen,
  $searchLoading,
  $searchResults,
  $sessions,
  $sessionSearch,
  $sessionsLoading,
  $sessionSort,
  $systemPresets,
  $systemPresetsFetched,
  $systemPresetsLoading,
  archiveSession,
  createNewSession,
  deleteSession,
  fetchArchived,
  fetchSessions,
  fetchSystemPresets,
  isCompanionSession,
  pinSession,
  renameSession,
  runSessionSearch,
  type SessionSort,
  setSessionSort,
  switchSession,
  TITLE_MAX_CHARS
} from '@/chat/session-list-store'
import {
  Archive,
  ArchiveOff,
  ArrowRight,
  CalendarPlus,
  Clock,
  Cpu,
  Globe,
  type IconComponent,
  List,
  Messages,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Trash2
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { INPUT_CLASS, SearchField } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import type { SessionInfo } from '@/shared/types/spiritagent'

import { PresetIconBadge, PresetPickerModal } from './preset-picker-modal'

function buildSortOptions(
  t: ReturnType<typeof useStrings>['workbench']['sessionSidebar']
): { icon: IconComponent; label: string; value: SessionSort }[] {
  return [
    { icon: Clock, label: t.sortOptions.recent, value: 'recent' },
    { icon: CalendarPlus, label: t.sortOptions.created, value: 'created' },
    { icon: Messages, label: t.sortOptions.messages, value: 'messages' }
  ]
}

function buildPresetMeta(
  t: ReturnType<typeof useStrings>['workbench']['sessionSidebar']['presetMeta']
): Record<string, { icon: IconComponent; label: string }> {
  return {
    copywriter: { icon: Pencil, label: t.copywriter },
    developer: { icon: Cpu, label: t.developer },
    language_teacher: { icon: Globe, label: t.language_teacher },
    product_manager: { icon: List, label: t.product_manager }
  }
}

function formatSessionTime(
  timestamp: number | undefined,
  tStrings: ReturnType<typeof useStrings>['workbench']['sessionSidebar']['time']
): string {
  if (!timestamp) {
    return tStrings.now
  }

  const date = new Date(timestamp > 1e11 ? timestamp : timestamp * 1000)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  if (isToday) {
    return tStrings.todayPrefix(timeStr)
  }

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)

  if (date.toDateString() === yesterday.toDateString()) {
    return tStrings.yesterdayPrefix(timeStr)
  }

  return tStrings.dateFormat(date.getMonth() + 1, date.getDate(), timeStr)
}

export function SessionSidebar(): React.JSX.Element {
  const sessions = useStore($sessions)
  const loading = useStore($sessionsLoading)
  const sort = useStore($sessionSort)
  const search = useStore($sessionSearch)
  const searchResults = useStore($searchResults)
  const searchLoading = useStore($searchLoading)
  const archivedSessions = useStore($archivedSessions)
  const archivedLoading = useStore($archivedLoading)
  const archiveOpen = useStore($archiveOpen)
  const activeSessionId = useStore($chatSessionId)
  const presets = useStore($systemPresets)
  const presetsLoading = useStore($systemPresetsLoading)
  const presetsFetched = useStore($systemPresetsFetched)
  const dict = useStrings()
  const t = dict.workbench.sessionSidebar
  const tActions = t.actions
  const tPreset = t.presetMeta
  const sortOptions = buildSortOptions(t)
  const workbenchPresetMeta = buildPresetMeta(tPreset)
  const [pickerOpen, setPickerOpen] = useState(false)

  const searchActive = search.trim().length > 0

  useEffect(() => {
    const q = search.trim()

    if (!q) {
      void runSessionSearch('')

      return
    }

    const timer = setTimeout(() => void runSessionSearch(q), 300)

    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    void fetchSessions()
    void fetchArchived()
  }, [])

  useEffect(() => {
    if (!presetsFetched) {
      void fetchSystemPresets()
    }
  }, [presetsFetched])

  const handleCreate = (): void => {
    setPickerOpen(true)
  }

  const handlePickerConfirm = async (presetId: string): Promise<void> => {
    setPickerOpen(false)
    await createNewSession(presetId)
  }

  const handleSwitch = async (id: string): Promise<void> => {
    await switchSession(id)
  }

  const isSpecialSession = (s: SessionInfo): boolean =>
    s.kind === 'special' || (typeof s.system_preset_id === 'string' && s.system_preset_id in workbenchPresetMeta)

  const workbenchSessions = sessions.filter(s => !isCompanionSession(s))

  // 工作台 4 套专业系统预设
  const specialSessions = workbenchSessions
    .filter(s => isSpecialSession(s))
    .sort((a, b) => {
      const order = ['developer', 'product_manager', 'copywriter', 'language_teacher']
      const aIdx = a.system_preset_id ? order.indexOf(a.system_preset_id) : 99
      const bIdx = b.system_preset_id ? order.indexOf(b.system_preset_id) : 99

      return aIdx - bIdx
    })

  // 用户常规会话（支持置顶与非置顶）
  const pinnedRegularSessions = workbenchSessions.filter(s => !isSpecialSession(s) && s.pinned)
  const unpinnedRegularSessions = workbenchSessions.filter(s => !isSpecialSession(s) && !s.pinned)

  // 过滤掉弹出框中的 companion
  const nonCompanionPresets = presets.filter(p => p.id !== 'companion')

  return (
    <aside className="flex h-full w-full min-h-0 flex-col overflow-hidden text-xs">
      {/* 顶部操作：Sessions 标题与 + 新建按钮 */}
      <div className="flex items-center justify-between px-3.5 pt-3 pb-1">
        <h3 className="text-sm font-semibold tracking-wide text-strong">{t.title}</h3>
        <button
          aria-label={t.new}
          className="flex size-6 items-center justify-center rounded-lg border border-line-standard bg-fill-faint text-muted transition hover:border-line-strong hover:bg-fill-hover hover:text-strong active:scale-95"
          onClick={handleCreate}
          title={t.new}
          type="button"
        >
          <Plus className="size-3.5" />
        </button>
      </div>

      {/* 搜索与排序 */}
      <div className="flex items-center justify-between gap-1.5 px-3 py-1.5">
        <div className="min-w-0 flex-1">
          <SearchField
            ariaLabel={t.searchAria}
            onChange={$sessionSearch.set}
            placeholder={t.searchPlaceholder}
            value={search}
          />
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {sortOptions.map(({ icon: Icon, label, value }) => (
            <button
              aria-label={label}
              className={cn(
                'rounded-lg p-1.5 transition-colors',
                value === sort
                  ? 'bg-fill-hover text-strong shadow-xs'
                  : 'text-muted hover:bg-fill-faint hover:text-strong'
              )}
              key={value}
              onClick={() => setSessionSort(value)}
              title={label}
              type="button"
            >
              <Icon className="size-3.5" />
            </button>
          ))}
        </div>
      </div>

      {/* 会话列表区域 */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-2.5 pb-3 scrollbar-thin">
        {searchActive ? (
          <div>
            <div className="mb-1 px-1.5 text-[11px] font-medium text-muted">{t.searchResultsHeading}</div>
            {searchLoading ? (
              <div className="py-6 text-center text-muted">{t.searching}</div>
            ) : searchResults.filter(s => !isCompanionSession(s)).length === 0 ? (
              <div className="py-6 text-center text-muted">{t.noMatch}</div>
            ) : (
              searchResults
                .filter(s => !isCompanionSession(s))
                .map(s => (
                  <SessionRow
                    badge={s.archived ? t.badgeArchived : undefined}
                    isActive={s.id === activeSessionId}
                    key={s.id}
                    onSwitch={handleSwitch}
                    session={s}
                  />
                ))
            )}
          </div>
        ) : (
          <>
            {/* 特殊对话：4 个专业工作预设 */}
            <div>
              <div className="mb-1.5 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>{t.specialHeading}</span>
                <span className="rounded bg-fill-faint px-1 py-0.2 text-[10px] text-muted">
                  {specialSessions.length}
                </span>
              </div>
              {loading && specialSessions.length === 0 ? (
                <div className="py-3 text-center text-[11px] text-muted">{t.loadingSpecial}</div>
              ) : specialSessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line-hairline p-3 text-center text-[11px] text-muted">
                  {t.noSpecial}
                </div>
              ) : (
                <div className="space-y-0.5">
                  {specialSessions.map(s => {
                    const meta = s.system_preset_id ? workbenchPresetMeta[s.system_preset_id] : undefined
                    const Icon = meta?.icon ?? Cpu

                    return (
                      <SessionRow
                        customIcon={Icon}
                        customLabel={meta?.label}
                        isActive={s.id === activeSessionId}
                        isSpecial
                        key={s.id}
                        onSwitch={handleSwitch}
                        session={s}
                      />
                    )
                  })}
                </div>
              )}
            </div>

            {/* 常规对话：用户自建与置顶会话 */}
            <div className="pt-1">
              <div className="mb-1.5 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>{t.regularHeading}</span>
                {pinnedRegularSessions.length + unpinnedRegularSessions.length > 0 && (
                  <span className="rounded bg-fill-faint px-1 py-0.2 text-[10px] text-muted">
                    {pinnedRegularSessions.length + unpinnedRegularSessions.length}
                  </span>
                )}
              </div>

              {loading && workbenchSessions.length === 0 ? (
                <div className="py-4 text-center text-muted">{t.loadingSessions}</div>
              ) : pinnedRegularSessions.length === 0 && unpinnedRegularSessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line-hairline p-4 text-center text-muted">
                  {t.noRegular}
                </div>
              ) : (
                <div className="space-y-0.5">
                  {pinnedRegularSessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <RowAction
                            icon={PinOff}
                            label={tActions.unpin}
                            onClick={e => stopThen(e, () => void pinSession(s.id, false))}
                          />
                          <RowAction
                            icon={Archive}
                            label={tActions.archive}
                            onClick={e => stopThen(e, () => void archiveSession(s.id, true))}
                          />
                        </>
                      }
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSwitch}
                      session={s}
                    />
                  ))}
                  {pinnedRegularSessions.length > 0 && unpinnedRegularSessions.length > 0 && (
                    <div className="my-1 border-t border-line-hairline opacity-40" />
                  )}
                  {unpinnedRegularSessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <RowAction
                            icon={Pin}
                            label={tActions.pin}
                            onClick={e => stopThen(e, () => void pinSession(s.id, true))}
                          />
                          <RowAction
                            icon={Archive}
                            label={tActions.archive}
                            onClick={e => stopThen(e, () => void archiveSession(s.id, true))}
                          />
                          <RowAction
                            danger
                            icon={Trash2}
                            label={tActions.delete}
                            onClick={e => stopThen(e, () => void deleteSession(s.id))}
                          />
                        </>
                      }
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSwitch}
                      session={s}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* 底部归档与展开切换 */}
      <div className="border-t border-line-hairline bg-transparent p-2.5">
        <button
          className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-line-standard bg-fill-faint py-1.5 text-xs text-muted transition hover:border-line-strong hover:bg-fill-hover hover:text-strong active:scale-98"
          onClick={() => $archiveOpen.set(!archiveOpen)}
          type="button"
        >
          <span>{archiveOpen ? t.archive.collapse : t.archive.expand(archivedSessions.length)}</span>
          <ArrowRight className="size-3" />
        </button>
        {archiveOpen && (
          <div className="mt-2 max-h-48 space-y-1 overflow-y-auto pr-0.5">
            {archivedLoading ? (
              <div className="py-2 text-center text-muted">{t.archive.loading}</div>
            ) : archivedSessions.length === 0 ? (
              <div className="py-2 text-center text-muted">{t.archive.empty}</div>
            ) : (
              archivedSessions.map(s => (
                <SessionRow
                  actions={
                    <>
                      <RowAction
                        icon={ArchiveOff}
                        label={tActions.restore}
                        onClick={e => stopThen(e, () => void archiveSession(s.id, false))}
                      />
                      <RowAction
                        danger
                        icon={Trash2}
                        label={tActions.delete}
                        onClick={e => stopThen(e, () => void deleteSession(s.id))}
                      />
                    </>
                  }
                  isActive={s.id === activeSessionId}
                  key={s.id}
                  onSwitch={handleSwitch}
                  session={s}
                />
              ))
            )}
          </div>
        )}
      </div>

      {pickerOpen && (
        <PresetPickerModal
          loading={presetsLoading}
          onClose={() => setPickerOpen(false)}
          onConfirm={handlePickerConfirm}
          presets={nonCompanionPresets}
        />
      )}
    </aside>
  )
}

function stopThen(e: React.MouseEvent, action: () => void): void {
  e.preventDefault()
  e.stopPropagation()
  action()
}

function SessionRow({
  session,
  isActive,
  isSpecial,
  customIcon: CustomIcon,
  customLabel,
  actions,
  badge,
  onSwitch
}: {
  session: SessionInfo
  isActive: boolean
  isSpecial?: boolean
  customIcon?: IconComponent
  customLabel?: string
  actions?: React.ReactNode
  badge?: string
  onSwitch: (id: string) => Promise<void> | void
}): React.JSX.Element {
  const canRename = !isSpecial && session.kind !== 'im'
  const [editing, setEditing] = useState(false)
  const presets = useStore($systemPresets)
  const dict = useStrings()
  const tSidebar = dict.workbench.sessionSidebar

  const presetName =
    customLabel ?? (session.system_preset_id ? presets.find(p => p.id === session.system_preset_id)?.name : undefined)

  const title =
    customLabel ??
    session.title ??
    (isSpecial ? (presetName ?? tSidebar.specialStationFallback) : tSidebar.newSessionFallback)

  const timeStr = formatSessionTime(session.last_active || session.started_at, tSidebar.time)
  const msgCount = session.message_count ?? 0

  return (
    <div
      className={cn(
        'group relative flex cursor-pointer flex-col gap-1 rounded-xl border p-2.5 transition-all duration-150 select-none',
        isActive
          ? 'border-line-standard bg-surface-card text-strong shadow-xs backdrop-blur-md'
          : 'border-transparent bg-transparent text-muted hover:border-line-hairline hover:bg-fill-hover hover:text-strong'
      )}
      onClick={() => {
        if (!editing) {
          void onSwitch(session.id)
        }
      }}
    >
      <div className="flex items-center justify-between gap-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {isActive ? (
            <span className="size-2 shrink-0 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.9)]" />
          ) : CustomIcon ? (
            <CustomIcon className="size-3.5 shrink-0 text-faint" />
          ) : isSpecial ? (
            <span className="shrink-0" title={presetName ?? ''}>
              <PresetIconBadge iconKey={session.system_preset_icon_key} />
            </span>
          ) : (
            <span className="size-1.5 shrink-0 rounded-full bg-fill-hover" />
          )}

          {editing ? (
            <SessionTitleInput
              initialTitle={session.title ?? ''}
              onCancel={() => setEditing(false)}
              onCommit={title => {
                setEditing(false)
                void renameSession(session.id, title)
              }}
            />
          ) : (
            <p className={cn('truncate text-xs font-medium', isActive ? 'font-semibold text-strong' : 'text-body')}>
              {title}
              {session.pinned && <Pin className="ml-1 inline size-2.5 text-accent opacity-75" />}
            </p>
          )}
        </div>

        <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
          {canRename && !editing && (
            <div className="opacity-0 transition-opacity group-hover:opacity-100">
              <RowAction
                icon={Pencil}
                label={dict.chat.sessionRename.action}
                onClick={e => stopThen(e, () => setEditing(true))}
              />
            </div>
          )}
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
            {actions}
          </div>
          {badge && <span className="rounded bg-fill-faint px-1 py-0.2 text-[9px] text-muted">{badge}</span>}
        </div>
      </div>

      <div className="flex items-center justify-between pl-4 text-[10.5px] text-faint">
        <span>{timeStr}</span>
        <span>{tSidebar.messageCount(msgCount)}</span>
      </div>
    </div>
  )
}

function SessionTitleInput({
  initialTitle,
  onCommit,
  onCancel
}: {
  initialTitle: string
  onCommit: (title: string) => void
  onCancel: () => void
}): React.JSX.Element {
  const [draft, setDraft] = useState(initialTitle)
  const doneRef = useRef(false)
  const dict = useStrings()

  const finish = (commit: boolean): void => {
    if (doneRef.current) {
      return
    }

    doneRef.current = true

    if (commit) {
      onCommit(draft)
    } else {
      onCancel()
    }
  }

  return (
    <input
      aria-label={dict.chat.sessionRename.inputLabel}
      autoFocus
      className={cn(INPUT_CLASS, 'h-6 px-1.5 py-0 text-xs')}
      maxLength={TITLE_MAX_CHARS}
      onBlur={() => finish(true)}
      onChange={e => setDraft(e.target.value)}
      onClick={e => e.stopPropagation()}
      onFocus={e => e.currentTarget.select()}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          finish(e.key === 'Enter')
        }
      }}
      placeholder={dict.chat.sessionRename.placeholder}
      title={dict.chat.sessionRename.hint}
      value={draft}
    />
  )
}

function RowAction({
  icon: Icon,
  label,
  danger,
  onClick
}: {
  icon: IconComponent
  label: string
  danger?: boolean
  onClick: (e: React.MouseEvent) => void
}): React.JSX.Element {
  return (
    <button
      aria-label={label}
      className={cn(
        'rounded-md p-1 transition-colors',
        danger
          ? 'text-faint hover:bg-rose-500/20 hover:text-rose-300'
          : 'text-faint hover:bg-fill-hover hover:text-strong'
      )}
      onClick={onClick}
      title={label}
      type="button"
    >
      <Icon className="size-3" />
    </button>
  )
}
