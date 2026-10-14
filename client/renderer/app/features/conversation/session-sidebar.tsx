import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionId } from '@/modules/conversation'
import {
  $archivedLoading,
  $archivedSessions,
  $archiveOpen,
  $searchLoading,
  $searchResults,
  $sessions,
  $sessionSearch,
  $sessionsError,
  $sessionsLoading,
  $sessionSort,
  $systemPresets,
  $systemPresetsFetched,
  $systemPresetsLoading,
  archiveSession,
  chatDisplayText,
  CompanionAvatar,
  createNewSession,
  deleteSession,
  fetchArchived,
  fetchSessions,
  fetchSystemPresets,
  forgetConversationDraft,
  getConversationRuntime,
  isCompanionSession,
  pendingMessages,
  pinSession,
  renameSession,
  retainConversationRuntime,
  runSessionSearch,
  sessionDisplayTitle,
  type SessionSort,
  setSessionSort,
  switchSession,
  TITLE_MAX_CHARS
} from '@/modules/conversation'
import {
  Archive,
  ArchiveOff,
  ArrowRight,
  CalendarPlus,
  Clock,
  type IconComponent,
  Loader2,
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
import type { SessionInfo } from '@protocol'

import { useConversationStrings } from './conversation-strings'
import { presetIcon, PresetPickerModal } from './preset-picker-modal'

const SPECIAL_PRESET_ORDER = ['copywriter', 'language_teacher']

function buildSortOptions(
  t: ReturnType<typeof useStrings>['conversation']['sessionSidebar']
): { icon: IconComponent; label: string; value: SessionSort }[] {
  return [
    { icon: Clock, label: t.sortOptions.recent, value: 'recent' },
    { icon: CalendarPlus, label: t.sortOptions.created, value: 'created' },
    { icon: Messages, label: t.sortOptions.messages, value: 'messages' }
  ]
}

function formatSessionTime(
  timestamp: number | undefined,
  tStrings: ReturnType<typeof useStrings>['conversation']['sessionSidebar']['time']
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

export function SessionSidebar({
  companionUnread = false,
  headerAction,
  onSessionSelected
}: {
  companionUnread?: boolean
  headerAction?: React.ReactNode
  onSessionSelected?: () => void
}): React.JSX.Element {
  const sessions = useStore($sessions)
  const loading = useStore($sessionsLoading)
  const sessionsError = useStore($sessionsError)
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
  const t = dict.conversation.sessionSidebar
  const tActions = t.actions
  const sortOptions = buildSortOptions(t)
  const [pickerOpen, setPickerOpen] = useState(false)
  const conversationStrings = useConversationStrings()

  const handleSelect = async (sessionId: string): Promise<void> => {
    onSessionSelected?.()
    await switchSession(sessionId)
  }

  const handleDelete = async (sessionId: string): Promise<void> => {
    if (await deleteSession(sessionId)) {
      forgetConversationDraft(sessionId)
    }
  }

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
    onSessionSelected?.()
  }

  // 固定对话只看 kind（PROTOCOL「会话种类与历史修改」）：普通会话与派生会话同样带预设，不能据此归入固定分组。
  const isSpecialSession = (s: SessionInfo): boolean => s.kind === 'special'

  const companionSessions = sessions.filter(s => isSpecialSession(s) && isCompanionSession(s))

  const specialSessions = sessions
    .filter(s => isSpecialSession(s) && !isCompanionSession(s))
    .sort((a, b) => {
      const aIdx = a.system_preset_id ? SPECIAL_PRESET_ORDER.indexOf(a.system_preset_id) : 99
      const bIdx = b.system_preset_id ? SPECIAL_PRESET_ORDER.indexOf(b.system_preset_id) : 99

      return (aIdx < 0 ? 99 : aIdx) - (bIdx < 0 ? 99 : bIdx)
    })

  // 用户常规会话（支持置顶与非置顶）
  const regularSessions = sessions.filter(s => !isSpecialSession(s))
  const pinnedRegularSessions = regularSessions.filter(s => s.pinned)
  const unpinnedRegularSessions = regularSessions.filter(s => !s.pinned)

  return (
    <aside className="flex h-full w-full min-h-0 flex-col overflow-hidden text-xs">
      {/* 顶部操作：Sessions 标题与 + 新建按钮 */}
      <div className="flex items-center justify-between px-3.5 pt-3 pb-1">
        <h3 className="text-sm font-semibold tracking-wide text-strong">{t.title}</h3>
        <div className="flex items-center gap-1">
          {headerAction}
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
        {sessionsError && (
          <div className="rounded-lg border border-line-standard bg-surface-card p-3 text-xs text-muted" role="alert">
            <p>{conversationStrings.loadFailed}</p>
            <p className="mt-1 break-words text-[11px]">{sessionsError}</p>
            <button
              className="mt-2 rounded-md px-2 py-1 text-accent hover:bg-fill-hover focus-visible:ring-1 focus-visible:ring-accent"
              onClick={() => void fetchSessions()}
              type="button"
            >
              {conversationStrings.retry}
            </button>
          </div>
        )}
        {searchActive ? (
          <div>
            <div className="mb-1 px-1.5 text-[11px] font-medium text-muted">{t.searchResultsHeading}</div>
            {searchLoading ? (
              <div className="py-6 text-center text-muted">{t.searching}</div>
            ) : searchResults.length === 0 ? (
              <div className="py-6 text-center text-muted">{t.noMatch}</div>
            ) : (
              searchResults.map(s => (
                <SessionRow
                  badge={s.archived ? t.badgeArchived : undefined}
                  isActive={s.id === activeSessionId}
                  key={s.id}
                  onSwitch={handleSelect}
                  session={s}
                  unread={companionUnread && isCompanionSession(s)}
                />
              ))
            )}
          </div>
        ) : (
          <>
            {companionSessions.length > 0 && (
              <div>
                <div className="mb-1.5 px-1.5 text-[11px] font-semibold text-muted">
                  {conversationStrings.companion}
                </div>
                {companionSessions.map(session => (
                  <SessionRow
                    isActive={session.id === activeSessionId}
                    key={session.id}
                    onSwitch={handleSelect}
                    session={session}
                    unread={companionUnread}
                  />
                ))}
              </div>
            )}
            <div>
              <div className="mb-1.5 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>{t.fixedHeading}</span>
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
                  {specialSessions.map(s => (
                    <SessionRow
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSelect}
                      session={s}
                      unread={companionUnread && isCompanionSession(s)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* 常规对话：用户自建与置顶会话 */}
            <div className="pt-1">
              <div className="mb-1.5 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>{t.regularHeading}</span>
                {regularSessions.length > 0 && (
                  <span className="rounded bg-fill-faint px-1 py-0.2 text-[10px] text-muted">
                    {regularSessions.length}
                  </span>
                )}
              </div>

              {loading && sessions.length === 0 ? (
                <div className="py-4 text-center text-muted">{t.loadingSessions}</div>
              ) : pinnedRegularSessions.length === 0 && unpinnedRegularSessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line-hairline p-4 text-center text-muted">
                  {t.noRegular}
                </div>
              ) : (
                <div className="space-y-0.5">
                  {pinnedRegularSessions.length > 0 && (
                    <div className="px-1.5 pt-1 text-[11px] font-semibold text-muted">{conversationStrings.pinned}</div>
                  )}
                  {pinnedRegularSessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <RowAction
                            icon={PinOff}
                            label={tActions.unpin}
                            onClick={() => void pinSession(s.id, false)}
                          />
                          <RowAction
                            icon={Archive}
                            label={tActions.archive}
                            onClick={() => void archiveSession(s.id, true)}
                          />
                          <RowAction
                            danger
                            icon={Trash2}
                            label={tActions.delete}
                            onClick={() => void handleDelete(s.id)}
                          />
                        </>
                      }
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSelect}
                      session={s}
                    />
                  ))}
                  {pinnedRegularSessions.length > 0 && unpinnedRegularSessions.length > 0 && (
                    <div className="my-1 border-t border-line-hairline opacity-40" />
                  )}
                  {unpinnedRegularSessions.length > 0 && (
                    <div className="px-1.5 pt-1 text-[11px] font-semibold text-muted">{conversationStrings.recent}</div>
                  )}
                  {unpinnedRegularSessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <RowAction icon={Pin} label={tActions.pin} onClick={() => void pinSession(s.id, true)} />
                          <RowAction
                            icon={Archive}
                            label={tActions.archive}
                            onClick={() => void archiveSession(s.id, true)}
                          />
                          <RowAction
                            danger
                            icon={Trash2}
                            label={tActions.delete}
                            onClick={() => void handleDelete(s.id)}
                          />
                        </>
                      }
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSelect}
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
                        onClick={() => void archiveSession(s.id, false)}
                      />
                      <RowAction danger icon={Trash2} label={tActions.delete} onClick={() => void handleDelete(s.id)} />
                    </>
                  }
                  isActive={s.id === activeSessionId}
                  key={s.id}
                  onSwitch={handleSelect}
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
          presets={presets}
        />
      )}
    </aside>
  )
}

function SessionRow({
  unread: companionUnread = false,
  session,
  isActive,
  actions,
  badge,
  onSwitch
}: {
  unread?: boolean
  session: SessionInfo
  isActive: boolean
  actions?: React.ReactNode
  badge?: string
  onSwitch: (id: string) => Promise<void> | void
}): React.JSX.Element {
  const special = session.kind === 'special'
  const canRename = !special
  const [editing, setEditing] = useState(false)
  const pending = useStore(pendingMessages.$atom)
  const runtime = getConversationRuntime(session.id)
  const turnInFlight = useStore(runtime.$chatTurnInFlight)
  const pendingBatch = useStore(runtime.$pendingPromptBatch)
  const running = turnInFlight || pendingBatch.length > 0
  const unread = companionUnread || pending.some(message => message.sessionId === session.id)
  const presets = useStore($systemPresets)
  const dict = useStrings()
  const tSidebar = dict.conversation.sessionSidebar
  const conversationStrings = useConversationStrings()

  useEffect(() => retainConversationRuntime(runtime), [runtime])

  // 固定预设会话在任何列表里都显示当前界面语言的预设名（含搜索结果）。
  const title =
    sessionDisplayTitle(dict, session, presets) ??
    (special ? tSidebar.specialStationFallback : tSidebar.newSessionFallback)

  const timeStr = formatSessionTime(session.last_active || session.started_at, tSidebar.time)
  const msgCount = session.message_count ?? 0
  const RowIcon = presetIcon(session.system_preset_icon_key)

  return (
    <div
      aria-current={isActive ? 'true' : undefined}
      aria-label={unread ? `${title} (${dict.living.rail.unread})` : title}
      className={cn(
        'group relative flex cursor-pointer flex-col gap-1 rounded-xl border p-2.5 transition-all duration-150 select-none',
        isActive
          ? 'border-line-standard bg-surface-card text-strong shadow-xs backdrop-blur-md'
          : 'border-transparent bg-transparent text-muted hover:border-line-hairline hover:bg-fill-hover hover:text-strong',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent',
        'min-h-[76px] rounded-lg py-3 pl-14 pr-3'
      )}
      data-chat-row
      onClick={() => {
        if (!editing) {
          void onSwitch(session.id)
        }
      }}
      onKeyDown={event => {
        if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          void onSwitch(session.id)
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="absolute left-3 top-3">
        {isCompanionSession(session) ? (
          <CompanionAvatar />
        ) : (
          <span className="grid size-8 place-items-center rounded-xl border border-line-hairline bg-accent-soft text-accent">
            <RowIcon size={17} />
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
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
            <p className={cn('truncate text-[13px] font-medium', isActive ? 'font-semibold text-strong' : 'text-body')}>
              {title}
              {session.pinned && <Pin className="ml-1 inline size-2.5 text-accent opacity-75" />}
            </p>
          )}
        </div>

        <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
          {running && (
            <Loader2
              aria-label={conversationStrings.running}
              className="size-3.5 animate-spin text-accent"
              role="status"
            />
          )}
          {canRename && !editing && (
            <div className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
              <RowAction icon={Pencil} label={dict.chat.sessionRename.action} onClick={() => setEditing(true)} />
            </div>
          )}
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            {actions}
          </div>
          {badge && <span className="rounded bg-fill-faint px-1 py-0.2 text-[9px] text-muted">{badge}</span>}
        </div>
      </div>

      <div className="flex min-w-0 items-center justify-between gap-2 text-[10.5px] text-faint">
        <span className="min-w-0 flex-1 truncate text-xs">
          {isCompanionSession(session)
            ? tSidebar.messageCount(msgCount)
            : chatDisplayText(session.preview ?? '').replace(/\s+/g, ' ') || tSidebar.messageCount(msgCount)}
        </span>
        {unread && <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-accent" />}
      </div>
      <span className="mt-0.5 text-[10px] text-faint">{timeStr}</span>
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
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      aria-label={label}
      className={cn(
        'rounded-md p-1 transition-colors',
        danger
          ? 'text-faint hover:bg-danger-bg hover:text-danger-fg'
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
