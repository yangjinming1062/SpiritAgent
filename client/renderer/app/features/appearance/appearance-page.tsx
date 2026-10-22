import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { useAssetLibrarySearch } from '@/app/components/asset-library-header'
import { $outfits, hydrateAvatarSeeds, hydrateWardrobe } from '@/modules/character'
import { $auth } from '@/shared/store/auth'

import { $appearanceUi, updateAppearanceUi } from './appearance-ui-state'
import { OutfitDetail } from './outfit-detail'
import { OutfitSection } from './outfit-section'

// 衣柜页按造型素材组织；窗口模式显示设置归设置页统一管理。
interface AppearancePageProps {
  startCreating?: boolean
  onMaterialReady?: (outfitId: number) => void
}

export function AppearancePage({
  startCreating = false,
  onMaterialReady
}: AppearancePageProps = {}): React.JSX.Element {
  const authKind = useStore($auth).kind
  const outfits = useStore($outfits)
  const { selectedOutfitId } = useStore($appearanceUi)
  const [loaded, setLoaded] = useState(false)
  const librarySearch = useAssetLibrarySearch()
  const setSelectedOutfitId = (id: number | null): void => updateAppearanceUi({ selectedOutfitId: id })

  useEffect(() => {
    if (startCreating) {
      updateAppearanceUi({ selectedOutfitId: null, designing: true })
    }
  }, [startCreating])

  // 衣柜列表在 auth 就绪后再水合——冷启动直接进入本页时 hydrateAuth 的 IPC 往返尚未完成，提前调用会因 pending 静默跳过。种子图走本地缓存，缺失时补拉。
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

  useEffect(() => {
    if (loaded && selectedOutfitId !== null && !outfits.some(outfit => outfit.id === selectedOutfitId)) {
      setSelectedOutfitId(null)
    }
  }, [loaded, outfits, selectedOutfitId])

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {selectedOutfitId === null ? (
        <OutfitSection onMaterialReady={onMaterialReady} onSelectOutfit={setSelectedOutfitId} search={librarySearch} />
      ) : (
        <OutfitDetail
          onBack={() => setSelectedOutfitId(null)}
          onMaterialReady={onMaterialReady}
          outfitId={selectedOutfitId}
        />
      )}
    </div>
  )
}
