import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $avatarSeeds, hydrateAvatarSeeds } from '@/features/character'
import { type PickedImage, resolvePortraitUrl } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import type { LocalSearch } from '@/shared/hooks/use-local-search'
import { requestResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import type { ImageReviseMode } from '@protocol'

import { $appearanceUi, selectAppearanceDraft, updateAppearanceUi } from './appearance-ui-state'
import { useOutfitDesignSession } from './design-session'
import { useOutfitLibrary } from './use-outfit-library'
import { hydrateWardrobe } from './wardrobe-store'

/** 造型列表与制作共用的状态、会话与命令；视图只消费返回值。 */
export function useOutfitSection({
  onSelectOutfit,
  search
}: {
  onSelectOutfit: (id: string) => void
  search: LocalSearch
}) {
  const dict = useStrings()
  const t = dict.living.outfit
  const common = dict.common
  const selfSourceDict = dict.selfSource
  const avatarSeeds = useStore($avatarSeeds)
  const ui = useStore($appearanceUi)
  const { text, draftId } = ui
  // 制作区与列表滚动位置随组件生命周期，不跨菜单保留。
  const [designing, setDesigning] = useState(false)
  const library = useOutfitLibrary({ restoreKey: designing })
  const { outfits, wardrobeStatus } = library
  const [zoomUrl, setZoomUrl] = useState<string | null>(null)
  const [failedPreview, setFailedPreview] = useState<string | null>(null)
  const [loadedPreview, setLoadedPreview] = useState<string | null>(null)
  const setText = (next: string): void => updateAppearanceUi({ text: next })
  const [outfitSelfSourceOpen, setOutfitSelfSourceOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const begin = useAsyncGuard()
  const visibleOutfits = outfits.filter(outfit => search.matches(outfit.name, outfit.description))

  // 确认入柜后重拉列表——新装转为参考图就绪。
  const session = useOutfitDesignSession(
    draftId,
    id => {
      if ($appearanceUi.get().draftId !== draftId) {
        return
      }

      updateAppearanceUi({
        text: '',
        draftId: null
      })
      setDesigning(false)
      onSelectOutfit(id)
      void hydrateWardrobe()
    },
    (id, submittedText) => {
      const current = $appearanceUi.get()
      const draftTexts = { ...current.draftTexts }
      const inputKey = draftId ?? 'new'

      if (draftTexts[inputKey]?.trim() === submittedText) {
        delete draftTexts[inputKey]
      }

      updateAppearanceUi({
        draftTexts,
        ...(current.draftId === draftId
          ? { draftId: id, text: current.text.trim() === submittedText ? '' : current.text }
          : {})
      })
    }
  )

  const currentDraft = session.draft
  const adoptDraft = session.adoptDraft

  useEffect(() => {
    if (draftId === null) {
      return
    }

    const saved = outfits.find(outfit => outfit.id === draftId)

    if (wardrobeStatus === 'loaded' && !session.busy && saved?.status !== 'draft') {
      updateAppearanceUi({
        draftId: null,
        text: ''
      })
      setDesigning(false)

      if (saved?.status === 'ready') {
        onSelectOutfit(saved.id)
      }

      return
    }

    if (saved?.status === 'draft' && (!currentDraft || (wardrobeStatus === 'loaded' && !session.busy))) {
      adoptDraft(saved.id, saved.fullbodyUrl ?? '', saved.fullbodyPath)
    }
  }, [adoptDraft, currentDraft, draftId, onSelectOutfit, outfits, session.busy, wardrobeStatus])

  useEffect(() => {
    messagesRef.current?.scrollTo?.({ top: messagesRef.current.scrollHeight })
  }, [session.messages])

  const startDesign = (): void => {
    selectAppearanceDraft(null)
    setDesigning(true)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const previewUrl = session.draft?.previewUrl ?? null
  const previewFailed = session.previewError || Boolean(previewUrl && failedPreview === previewUrl)
  const creationLocked = session.busy || session.resultUnknown || previewFailed
  const latestMessage = session.messages.at(-1)
  const hasInput = !!text.trim() || !!session.refImage

  const viewWardrobe = (): void => {
    void hydrateWardrobe({ revalidateAssets: previewFailed })
    setDesigning(false)
  }

  // 首次生成（无草稿）：描述/参考图创建新设计。
  const sendCreation = (): void => {
    // 生成进行中会话内部会拒绝——此时不清空输入，避免丢字。
    if (creationLocked || !hasInput) {
      return
    }

    session.send(text, 'edit')
  }

  // 草稿反馈的两个显式操作（DESIGN「形象获取与确认」）：微调编辑上一版须带反馈，重新生成允许空反馈整体重绘。
  const sendRevise = (mode: ImageReviseMode): void => {
    if (creationLocked || (mode === 'edit' && !text.trim())) {
      return
    }

    session.send(text, mode)
  }

  // 自备图（着装立绘）：有草稿时提示词/采纳都走草稿重绘语境，否则走创建语境。
  const fetchOutfitSelfSourcePrompt = async (): Promise<string> => {
    const res = await requestResource('companion.post_outfit_prompt', {
      body: session.draft
        ? { outfit_id: session.draft.id, feedback: text.trim() || undefined }
        : {
            description: text.trim() || undefined,
            image: session.refImage?.base64,
            content_type: session.refImage?.contentType
          }
    })

    return res.prompt
  }

  const adoptOutfitSelfSource = async (image: PickedImage): Promise<void> => {
    const isLive = begin()
    const selectedDraftId = $appearanceUi.get().draftId

    const res = session.draft
      ? await requestResource('companion.post_outfit_regenerate', {
          path: { outfit_id: session.draft.id },
          body: { source: 'analysis', image: image.base64, content_type: image.contentType }
        })
      : await requestResource('companion.post_outfit', {
          body: {
            source: 'analysis',
            image: image.base64,
            content_type: image.contentType,
            description: text.trim() || undefined
          }
        })

    if (!isLive()) {
      return
    }

    void hydrateWardrobe({ force: true })

    const resolved = res?.fullbody_url ? await resolvePortraitUrl(res.fullbody_url) : null

    if (!isLive() || selectedDraftId !== $appearanceUi.get().draftId) {
      return
    }

    // 从采纳后的草稿续上设计会话，可继续描述微调或直接确认入柜。
    if (res?.id) {
      updateAppearanceUi({ draftId: res.id })
      setDesigning(true)
      session.adoptDraft(res.id, resolved ?? '', res.fullbody_url)
    }

    setText('')
  }

  const outfitSelfSourceReferences = avatarSeeds.fullbodySeedUrl
    ? [{ label: selfSourceDict.refs.fullbodySeed, url: avatarSeeds.fullbodySeedUrl }]
    : undefined

  return {
    adoptOutfitSelfSource,
    busyId: library.busyId,
    onSelectOutfit,
    common,
    completeDescription: library.completeDescription,
    creationLocked,
    designing,
    dict,
    failedPreview,
    fetchOutfitSelfSourcePrompt,
    hasInput,
    hydrateAvatarSeeds,
    inputRef,
    latestMessage,
    libraryRef: library.libraryRef,
    loadedPreview,
    messagesRef,
    outfitPolicy: library.outfitPolicy,
    outfitSelfSourceOpen,
    outfitSelfSourceReferences,
    policyBusy: library.policyBusy,
    previewFailed,
    previewUrl,
    removeOutfit: library.removeOutfit,
    search,
    selfSourceDict,
    sendCreation,
    sendRevise,
    session,
    setDesigning,
    setFailedPreview,
    setLoadedPreview,
    setOutfitSelfSourceOpen,
    setText,
    setZoomUrl,
    startDesign,
    t,
    text,
    togglePolicy: library.togglePolicy,
    viewWardrobe,
    visibleOutfits,
    wardrobeStatus,
    zoomUrl
  }
}

export type OutfitSectionModel = ReturnType<typeof useOutfitSection>
