import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { useAssetLibrarySearch } from '@/app/components/asset-library-header'
import { useNavigationDetail } from '@/app/navigation/navigation-detail'
import { $outfits, hydrateAvatarSeeds, hydrateWardrobe } from '@/modules/character'
import { $auth } from '@/shared/store/auth'

import { OutfitDetail } from './outfit-detail'
import { OutfitSection } from './outfit-section'

export function AppearancePage(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const outfits = useStore($outfits)

  // 着装详情随组件生命周期；导航指向衣柜分区时才消费 outfitId 直达，离开的导航不回退详情。
  const [selectedOutfitId, setSelectedOutfitId] = useNavigationDetail<string | null>(
    target =>
      target.location.page === 'creation' && target.location.section === 'wardrobe'
        ? target.focus.page === 'creation'
          ? (target.focus.outfitId ?? null)
          : null
        : undefined,
    null
  )

  const [loaded, setLoaded] = useState(false)
  const librarySearch = useAssetLibrarySearch()

  // 冷启动须等待鉴权，否则水合会跳过读取。
  useEffect(() => {
    if (authKind === 'authenticated') {
      let current = true
      void hydrateWardrobe().finally(() => {
        if (current) {
          setLoaded(true)
        }
      })
      void hydrateAvatarSeeds()

      return () => {
        current = false
      }
    }

    setLoaded(false)
  }, [authKind])

  // 详情指向的着装不存在（已删除或未入柜）时退回列表。
  useEffect(() => {
    if (loaded && selectedOutfitId !== null && !outfits.some(outfit => outfit.id === selectedOutfitId)) {
      setSelectedOutfitId(null)
    }
  }, [loaded, outfits, selectedOutfitId, setSelectedOutfitId])

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {selectedOutfitId === null ? (
        <OutfitSection onSelectOutfit={setSelectedOutfitId} search={librarySearch} />
      ) : (
        <OutfitDetail onBack={() => setSelectedOutfitId(null)} outfitId={selectedOutfitId} />
      )}
    </div>
  )
}
