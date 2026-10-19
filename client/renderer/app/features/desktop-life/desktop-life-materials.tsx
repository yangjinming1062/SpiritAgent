import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $outfits, $wardrobeStatus, hydrateWardrobe } from '@/modules/character'
import type { DesktopVideoPromptReference } from '@/modules/desktop-videos'
import { useResolvedMediaSrc } from '@/modules/media'
import {
  $sceneLibrary,
  $sceneLibraryStatus,
  $scenePage,
  $sceneQuery,
  $sceneTotal,
  loadSceneLibrary,
  PAGE_SIZE,
  type SceneAsset
} from '@/modules/scene'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useImageActions } from '@/shared/hooks/use-image-actions'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { Check, ImageIcon, Loader2, Search, Shirt, Sparkles } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { BTN_GHOST, BTN_SUBTLE, INPUT_CLASS, WizardModal } from '@/shared/panel'
import { fileApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'

import { BTN_GHOST as DESKTOP_LIFE_BTN_GHOST } from './desktop-life-buttons'
import styles from './desktop-life-materials.module.css'
import { useDesktopLifeStrings } from './desktop-life-strings'

type MaterialKind = 'scene' | 'outfit'
type OutfitReference = Pick<ReturnType<typeof $outfits.get>[number], 'id' | 'name' | 'fullbodyPath' | 'status'>

interface DesktopLifeReferencesProps {
  scene: SceneAsset | null
  sceneId: string
  sceneLabel?: string
  outfit: OutfitReference | undefined
  outfitId: string
  disabled: boolean
  onPickScene: (scene: SceneAsset) => void
  onPickOutfit: (id: string) => void
  onPrepareMaterial?: (kind: MaterialKind) => void
}

function ReferenceImage({ url, kind }: { url: string | null; kind: MaterialKind }): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: url ?? '' })
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const Icon = kind === 'scene' ? ImageIcon : Shirt
  const failed = media.status === 'failed' || (Boolean(url) && failedUrl === url)

  return (
    <div className={`${styles.image} ${kind === 'outfit' ? styles.outfitImage : ''}`}>
      {url && media.status === 'ready' && !failed ? (
        <img alt="" onError={() => setFailedUrl(url)} src={media.src} />
      ) : (
        <span className={styles.imagePlaceholder}>
          {url && media.status === 'loading' ? (
            <Loader2 aria-hidden="true" className={styles.spinner} size={24} />
          ) : (
            <Icon aria-hidden="true" size={26} strokeWidth={1.5} />
          )}
          <span>{failed ? t.materialPreviewFailed : url ? t.loading : t.noPreview}</span>
        </span>
      )}
    </div>
  )
}

function MaterialPicker({
  kind,
  selectedId,
  onClose,
  onPickScene,
  onPickOutfit,
  onPrepare
}: {
  kind: MaterialKind
  selectedId: string
  onClose: () => void
  onPickScene: (scene: SceneAsset) => void
  onPickOutfit: (id: string) => void
  onPrepare?: () => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const scenes = useStore($sceneLibrary)
  const sceneStatus = useStore($sceneLibraryStatus)
  const scenePage = useStore($scenePage)
  const sceneQuery = useStore($sceneQuery)
  const sceneTotal = useStore($sceneTotal)
  const outfits = useStore($outfits)
  const wardrobeStatus = useStore($wardrobeStatus)
  const beginAsync = useAsyncGuard()
  const [query, setQuery] = useState(kind === 'scene' ? $sceneQuery.get() : '')
  const [outfitQuery, setOutfitQuery] = useState('')
  const [unexpectedFailure, setUnexpectedFailure] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const loadRequest = useRef(0)

  const loadMaterials = useCallback(
    (nextQuery = $sceneQuery.get(), page = $scenePage.get()): void => {
      const scope = captureAuthScope()
      const isLive = beginAsync()
      const request = ++loadRequest.current
      setUnexpectedFailure(false)
      const loading = kind === 'scene' ? loadSceneLibrary(nextQuery, page) : hydrateWardrobe()

      void loading.catch(error => {
        if (request === loadRequest.current && isLive() && scope?.()) {
          log.warn('desktop-life', `Reference ${kind} library could not be loaded:`, error)
          setUnexpectedFailure(true)
        }
      })
    },
    [beginAsync, kind]
  )

  useEffect(() => {
    loadMaterials()
  }, [loadMaterials])

  useEffect(() => {
    searchRef.current?.focus()
    const dialog = contentRef.current?.closest<HTMLElement>('[role="dialog"]')

    const trapFocus = (event: KeyboardEvent): void => {
      if (event.key !== 'Tab' || !dialog) {
        return
      }

      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')
      ).filter(element => element.getClientRects().length > 0)

      const first = focusable[0]
      const last = focusable.at(-1)

      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault()
        first?.focus()
      }
    }

    window.addEventListener('keydown', trapFocus, true)

    return () => window.removeEventListener('keydown', trapFocus, true)
  }, [])

  const search = (): void => {
    if (kind === 'scene') {
      loadMaterials(query.trim(), 0)
    } else {
      setOutfitQuery(query.trim())
    }
  }

  const readyScenes = scenes.filter(scene => scene.status === 'ready')
  const readyOutfits = outfits.filter(outfit => outfit.status === 'ready')

  const matchedOutfits = readyOutfits.filter(outfit =>
    outfit.name.toLocaleLowerCase().includes(outfitQuery.toLocaleLowerCase())
  )

  const status = kind === 'scene' ? sceneStatus : wardrobeStatus
  const failed = unexpectedFailure || status === 'error'
  const loading = !failed && (status === 'loading' || status === 'idle')
  const count = kind === 'scene' ? readyScenes.length : matchedOutfits.length
  const hasQuery = Boolean(kind === 'scene' ? sceneQuery : outfitQuery)

  return (
    <WizardModal
      footer={
        <div className={styles.pickerFooter}>
          {kind === 'scene' && (
            <div className={styles.pagination}>
              <button
                className={`${BTN_GHOST} ${styles.button}`}
                disabled={loading || scenePage === 0}
                onClick={() => loadMaterials(sceneQuery, scenePage - 1)}
                type="button"
              >
                {t.previous}
              </button>
              <span className={styles.pageNumber}>
                {scenePage + 1} / {Math.max(1, Math.ceil(sceneTotal / PAGE_SIZE))}
              </span>
              <button
                className={`${BTN_GHOST} ${styles.button}`}
                disabled={loading || (scenePage + 1) * PAGE_SIZE >= sceneTotal}
                onClick={() => loadMaterials(sceneQuery, scenePage + 1)}
                type="button"
              >
                {t.next}
              </button>
            </div>
          )}
          {onPrepare && (
            <button className={`${BTN_GHOST} ${styles.button}`} onClick={onPrepare} type="button">
              <Sparkles aria-hidden="true" size={16} />
              {kind === 'scene' ? t.prepareScene : t.prepareOutfit}
            </button>
          )}
        </div>
      }
      onClose={onClose}
      regionId={`desktop-life-${kind}-picker`}
      title={kind === 'scene' ? t.chooseScene : t.chooseOutfit}
      widthClass="max-w-3xl"
    >
      <div className={styles.picker} ref={contentRef}>
        <div className={styles.search} role="search">
          <div className={styles.searchField}>
            <Search aria-hidden="true" className={styles.searchIcon} size={17} />
            <input
              aria-label={kind === 'scene' ? t.searchScenes : t.searchOutfits}
              className={`${INPUT_CLASS} ${styles.searchInput}`}
              onChange={event => setQuery(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  search()
                }
              }}
              placeholder={kind === 'scene' ? t.searchScenes : t.searchOutfits}
              ref={searchRef}
              type="search"
              value={query}
            />
          </div>
          <button className={`${BTN_SUBTLE} ${styles.button}`} disabled={loading} onClick={search} type="button">
            {t.search}
          </button>
        </div>
        {failed ? (
          <div className={styles.empty} role="alert">
            <p>{t.materialLoadFailed}</p>
            <button className={`${BTN_SUBTLE} ${styles.button}`} onClick={() => loadMaterials()} type="button">
              {t.refresh}
            </button>
          </div>
        ) : loading ? (
          <div className={styles.empty} role="status">
            <Loader2 aria-hidden="true" className={styles.spinner} size={25} />
            <p>{t.loading}</p>
          </div>
        ) : !count ? (
          <div className={styles.empty} role="status">
            {kind === 'scene' ? (
              <ImageIcon aria-hidden="true" size={30} strokeWidth={1.5} />
            ) : (
              <Shirt aria-hidden="true" size={30} strokeWidth={1.5} />
            )}
            <p>{hasQuery ? t.noMaterialResults : kind === 'scene' ? t.noSceneMaterials : t.noOutfitMaterials}</p>
          </div>
        ) : (
          <div className={styles.materialGrid}>
            {kind === 'scene'
              ? readyScenes.map(scene => (
                  <button
                    aria-pressed={scene.id === selectedId}
                    className={styles.materialOption}
                    key={scene.id}
                    onClick={() => onPickScene(scene)}
                    title={scene.title}
                    type="button"
                  >
                    <ReferenceImage kind="scene" url={scene.assetUrl || scene.thumbnailUrl || null} />
                    <span className={styles.optionLabel}>
                      <span className={styles.optionName}>{scene.title}</span>
                    </span>
                    {scene.id === selectedId && (
                      <span className={styles.selected}>
                        <Check aria-hidden="true" size={14} />
                        {t.materialSelected}
                      </span>
                    )}
                  </button>
                ))
              : matchedOutfits.map(outfit => (
                  <button
                    aria-pressed={String(outfit.id) === selectedId}
                    className={styles.materialOption}
                    key={outfit.id}
                    onClick={() => onPickOutfit(String(outfit.id))}
                    title={outfit.name}
                    type="button"
                  >
                    <ReferenceImage kind="outfit" url={outfit.fullbodyUrl || outfit.fullbodyPath} />
                    <span className={styles.optionLabel}>
                      <span className={styles.optionName}>{outfit.name}</span>
                    </span>
                    {String(outfit.id) === selectedId && (
                      <span className={styles.selected}>
                        <Check aria-hidden="true" size={14} />
                        {t.materialSelected}
                      </span>
                    )}
                  </button>
                ))}
          </div>
        )}
      </div>
    </WizardModal>
  )
}

export function DesktopLifeReferences({
  scene,
  sceneId,
  sceneLabel,
  outfit,
  outfitId,
  disabled,
  onPickScene,
  onPickOutfit,
  onPrepareMaterial
}: DesktopLifeReferencesProps): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const active = usePanelActivity()
  const auth = useStore($auth)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const [picker, setPicker] = useState<{ kind: MaterialKind; sessionId: string } | null>(null)
  const sceneTrigger = useRef<HTMLButtonElement>(null)
  const outfitTrigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!active || disabled || picker?.sessionId !== sessionId) {
      setPicker(null)
    }
  }, [active, disabled, picker?.sessionId, sessionId])

  const closePicker = (restoreFocus = true): void => {
    const trigger = picker?.kind === 'scene' ? sceneTrigger.current : outfitTrigger.current
    setPicker(null)

    if (restoreFocus && mayPick() && trigger?.isConnected) {
      trigger.focus()
    }
  }

  const mayPick = (): boolean => {
    const currentAuth = $auth.get()

    return active && !disabled && currentAuth.kind === 'authenticated' && currentAuth.snapshot.sessionId === sessionId
  }

  const openPicker = (kind: MaterialKind): void => {
    if (mayPick() && sessionId) {
      setPicker({ kind, sessionId })
    }
  }

  const sceneName = scene?.title || sceneLabel || t.sceneMissing
  const outfitName = outfit?.name || t.outfitMissing
  const visiblePicker = active && !disabled && picker && picker.sessionId === sessionId ? picker : null

  return (
    <section className={styles.references}>
      <p className={styles.hint}>{t.referencesHint}</p>
      <div className={styles.referenceGrid}>
        <article className={styles.referenceCard}>
          <div className={styles.cardHeading}>
            <ImageIcon aria-hidden="true" size={16} />
            <h3>{t.referenceScene}</h3>
          </div>
          <ReferenceImage kind="scene" url={scene?.assetUrl || scene?.thumbnailUrl || null} />
          <div className={styles.cardFooter}>
            <p className={styles.name} title={sceneName}>
              {sceneName}
            </p>
            <button
              aria-haspopup="dialog"
              aria-label={`${t.changeMaterial} · ${t.referenceScene}`}
              className={`${BTN_SUBTLE} ${styles.button}`}
              disabled={disabled || !active || !sessionId}
              onClick={() => openPicker('scene')}
              ref={sceneTrigger}
              type="button"
            >
              {t.changeMaterial}
            </button>
          </div>
          {scene?.status !== 'ready' && onPrepareMaterial && (
            <button
              className={`${BTN_GHOST} ${styles.prepareButton}`}
              disabled={disabled || !active || !sessionId}
              onClick={() => {
                if (mayPick()) {
                  onPrepareMaterial('scene')
                }
              }}
              type="button"
            >
              <Sparkles aria-hidden="true" size={15} />
              {t.prepareScene}
            </button>
          )}
        </article>
        <article className={styles.referenceCard}>
          <div className={styles.cardHeading}>
            <Shirt aria-hidden="true" size={16} />
            <h3>{t.referenceOutfit}</h3>
          </div>
          <ReferenceImage kind="outfit" url={outfit?.fullbodyPath || null} />
          <div className={styles.cardFooter}>
            <p className={styles.name} title={outfitName}>
              {outfitName}
            </p>
            <button
              aria-haspopup="dialog"
              aria-label={`${t.changeMaterial} · ${t.referenceOutfit}`}
              className={`${BTN_SUBTLE} ${styles.button}`}
              disabled={disabled || !active || !sessionId}
              onClick={() => openPicker('outfit')}
              ref={outfitTrigger}
              type="button"
            >
              {t.changeMaterial}
            </button>
          </div>
          {outfit?.status !== 'ready' && onPrepareMaterial && (
            <button
              className={`${BTN_GHOST} ${styles.prepareButton}`}
              disabled={disabled || !active || !sessionId}
              onClick={() => {
                if (mayPick()) {
                  onPrepareMaterial('outfit')
                }
              }}
              type="button"
            >
              <Sparkles aria-hidden="true" size={15} />
              {t.prepareOutfit}
            </button>
          )}
        </article>
      </div>
      {visiblePicker && (
        <MaterialPicker
          key={`${visiblePicker.sessionId}:${visiblePicker.kind}`}
          kind={visiblePicker.kind}
          onClose={() => closePicker()}
          onPickOutfit={id => {
            if (mayPick()) {
              closePicker()
              onPickOutfit(id)
            }
          }}
          onPickScene={selected => {
            if (mayPick()) {
              closePicker()
              onPickScene(selected)
            }
          }}
          onPrepare={
            onPrepareMaterial
              ? () => {
                  if (mayPick()) {
                    closePicker(false)
                    onPrepareMaterial(visiblePicker.kind)
                  }
                }
              : undefined
          }
          selectedId={visiblePicker.kind === 'scene' ? sceneId : outfitId}
        />
      )}
    </section>
  )
}

export function ExternalReference({
  reference,
  onZoom
}: {
  reference: DesktopVideoPromptReference
  onZoom: (url: string) => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: reference.url })
  const { copied, copy, error, reset } = useImageActions()
  const [saved, setSaved] = useState(false)
  const beginAsync = useAsyncGuard()

  const save = async (): Promise<void> => {
    if (media.status !== 'ready') {
      return
    }

    const isLive = beginAsync()
    reset()

    try {
      if ((await fileApi().saveImage({ defaultName: reference.label, url: media.src })) && isLive()) {
        setSaved(true)
      }
    } catch (failure) {
      if (isLive()) {
        notifyError(failure, t.saveReference)
      }
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-lg border border-line-hairline p-2">
      <div className="flex h-24 shrink-0 items-center justify-center overflow-hidden rounded bg-black/10">
        {media.status === 'ready' ? (
          <button
            aria-label={reference.label}
            className="h-full w-full cursor-zoom-in"
            onClick={() => onZoom(media.src)}
            type="button"
          >
            <img alt={reference.label} className="h-full w-full object-contain" src={media.src} />
          </button>
        ) : (
          <span className="text-xs text-muted">{reference.label}</span>
        )}
      </div>
      <span className="truncate text-xs text-body" title={reference.label}>
        {reference.label}
      </span>
      <div className="flex flex-wrap gap-1">
        <button
          aria-label={`${t.copyReference} ${reference.label}`}
          className={DESKTOP_LIFE_BTN_GHOST}
          disabled={media.status !== 'ready'}
          onClick={() => {
            if (media.status === 'ready') {
              void copy(media.src)
            }
          }}
          type="button"
        >
          {copied ? t.copied : t.copyReference}
        </button>
        <button
          aria-label={`${t.saveReference} ${reference.label}`}
          className={DESKTOP_LIFE_BTN_GHOST}
          disabled={media.status !== 'ready'}
          onClick={() => void save()}
          type="button"
        >
          {saved ? t.referenceSaved : t.saveReference}
        </button>
      </div>
      {error === 'copy' && (
        <p className="text-xs text-rose-500" role="alert">
          {t.copyReferenceFailed}
        </p>
      )}
    </div>
  )
}
