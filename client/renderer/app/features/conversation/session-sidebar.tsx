import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionId } from '@/modules/conversation'
import {
  $archivedLoading,
  $archivedNextCursor,
  $archivedSessions,
  $archiveOpen,
  $searchLoading,
  $searchNextCursor,
  $searchResults,
  $sessions,
  $sessionSearch,
  $sessionsError,
  $sessionsLoading,
  $sessionsNextCursor,
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
  Messages,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Trash2
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { Badge, ErrorBanner, IconAction, INPUT_CLASS, SearchField, Spinner } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'
import type { SessionInfo } from '@protocol'

import { presetIcon, PresetPickerModal } from './preset-picker-modal'

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

  const date = new Date(timestamp)
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
  companionSessionId,
  companionUnread = false,
  headerAction,
  onSessionSelected
}: {
  companionSessionId: string | null
  companionUnread?: boolean
  headerAction?: React.ReactNode
  onSessionSelected?: () => void
}): React.JSX.Element {
  const auth = useStore($auth)
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const sessions = useStore($sessions)
  const loading = useStore($sessionsLoading)
  const sessionsNextCursor = useStore($sessionsNextCursor)
  const searchNextCursor = useStore($searchNextCursor)
  const archivedNextCursor = useStore($archivedNextCursor)
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
  const strings = useStrings()
  const conversationStrings = strings.chat.nav

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
    if (!authSessionId) {
      return
    }

    const q = search.trim()

    if (!q) {
      void runSessionSearch('')

      return
    }

    const timer = setTimeout(() => void runSessionSearch(q), 300)

    return () => clearTimeout(timer)
  }, [authSessionId, search])

  useEffect(() => {
    if (!authSessionId) {
      return
    }

    void fetchSessions()
    void fetchArchived()
  }, [authSessionId])

  useEffect(() => {
    if (authSessionId && !presetsFetched) {
      void fetchSystemPresets()
    }
  }, [authSessionId, presetsFetched])

  const handleCreate = (): void => {
    setPickerOpen(true)
  }

  const handlePickerConfirm = async (presetId: string): Promise<void> => {
    setPickerOpen(false)
    await createNewSession(presetId)
    onSessionSelected?.()
  }

  const systemSessions = sessions.filter(s => s.kind === 'special')
  const ordinarySessions = sessions.filter(s => s.kind !== 'special')
  const pinnedOrdinarySessions = ordinarySessions.filter(s => s.pinned)
  const unpinnedOrdinarySessions = ordinarySessions.filter(s => !s.pinned)

  return (
    <aside className="flex h-full w-full min-h-0 flex-col overflow-hidden text-xs">
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

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-2.5 pb-3 scrollbar-thin">
        {sessionsError && (
          <ErrorBanner
            detail={sessionsError}
            message={conversationStrings.loadFailed}
            onRetry={() => void fetchSessions()}
            retryLabel={strings.common.retry}
            tone="danger"
          />
        )}
        {searchActive ? (
          <div>
            <div className="mb-1 px-1.5 text-[11px] font-medium text-muted">{t.searchResultsHeading}</div>
            {searchLoading && searchResults.length === 0 ? (
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
                  unread={companionUnread && s.id === companionSessionId}
                />
              ))
            )}
          </div>
        ) : (
          <>
            <div>
              <div className="mb-1.5 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>{t.systemHeading}</span>
                <Badge>{systemSessions.length}</Badge>
              </div>
              {loading && systemSessions.length === 0 ? (
                <div className="py-3 text-center text-[11px] text-muted">{t.loadingSystem}</div>
              ) : systemSessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line-hairline p-3 text-center text-[11px] text-muted">
                  {t.noSystem}
                </div>
              ) : (
                <div className="space-y-0.5">
                  {systemSessions.map(s => (
                    <SessionRow
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSelect}
                      session={s}
                      unread={companionUnread && s.id === companionSessionId}
                    />
                  ))}
                </div>
              )}
            </div>

            <div className="pt-1">
              <div className="mb-1.5 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>{t.ordinaryHeading}</span>
                {ordinarySessions.length > 0 && <Badge>{ordinarySessions.length}</Badge>}
              </div>

              {loading && sessions.length === 0 ? (
                <div className="py-4 text-center text-muted">{t.loadingSessions}</div>
              ) : pinnedOrdinarySessions.length === 0 && unpinnedOrdinarySessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line-hairline p-4 text-center text-muted">
                  {t.noOrdinary}
                </div>
              ) : (
                <div className="space-y-0.5">
                  {pinnedOrdinarySessions.length > 0 && (
                    <div className="px-1.5 pt-1 text-[11px] font-semibold text-muted">{conversationStrings.pinned}</div>
                  )}
                  {pinnedOrdinarySessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <IconAction
                            icon={PinOff}
                            label={tActions.unpin}
                            onClick={() => void pinSession(s.id, false)}
                          />
                          <IconAction
                            icon={Archive}
                            label={tActions.archive}
                            onClick={() => void archiveSession(s.id, true)}
                          />
                          <IconAction
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
                  {pinnedOrdinarySessions.length > 0 && unpinnedOrdinarySessions.length > 0 && (
                    <div className="my-1 border-t border-line-hairline opacity-40" />
                  )}
                  {unpinnedOrdinarySessions.length > 0 && (
                    <div className="px-1.5 pt-1 text-[11px] font-semibold text-muted">{conversationStrings.recent}</div>
                  )}
                  {unpinnedOrdinarySessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <IconAction icon={Pin} label={tActions.pin} onClick={() => void pinSession(s.id, true)} />
                          <IconAction
                            icon={Archive}
                            label={tActions.archive}
                            onClick={() => void archiveSession(s.id, true)}
                          />
                          <IconAction
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
        {(searchActive ? searchNextCursor : sessionsNextCursor) && (
          <button
            className="w-full rounded-xl py-2 text-xs text-muted"
            disabled={searchActive ? searchLoading : loading}
            onClick={() => void (searchActive ? runSessionSearch(search, true) : fetchSessions(true))}
            type="button"
          >
            {t.loadMore}
          </button>
        )}
      </div>

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
            {archivedLoading && archivedSessions.length === 0 ? (
              <div className="py-2 text-center text-muted">{t.archive.loading}</div>
            ) : archivedSessions.length === 0 ? (
              <div className="py-2 text-center text-muted">{t.archive.empty}</div>
            ) : (
              archivedSessions.map(s => (
                <SessionRow
                  actions={
                    <>
                      <IconAction
                        icon={ArchiveOff}
                        label={tActions.restore}
                        onClick={() => void archiveSession(s.id, false)}
                      />
                      <IconAction
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
              ))
            )}
            {archivedNextCursor && (
              <button
                className="w-full rounded-xl py-2 text-xs text-muted"
                disabled={archivedLoading}
                onClick={() => void fetchArchived(true)}
                type="button"
              >
                {t.loadMore}
              </button>
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
  const dict = useStrings()
  const tSidebar = dict.conversation.sessionSidebar
  const strings = useStrings()
  const conversationStrings = strings.chat.nav

  useEffect(() => retainConversationRuntime(runtime), [runtime])

  const title = sessionDisplayTitle(dict, session) ?? tSidebar.newSessionFallback

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
            <Spinner
              ariaLabel={conversationStrings.running}
              className="shrink-0"
              role="status"
              size="sm"
              tone="accent"
            />
          )}
          {canRename && !editing && (
            <div className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
              <IconAction icon={Pencil} label={dict.chat.sessionRename.action} onClick={() => setEditing(true)} />
            </div>
          )}
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            {actions}
          </div>
          {badge && <Badge tone="count">{badge}</Badge>}
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
