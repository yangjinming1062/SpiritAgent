import { cn } from '@/shared/lib/utils'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

export function BrandMark({ className, ...props }: React.ComponentProps<'span'>): React.JSX.Element {
  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img alt="" className="size-full object-contain" src={assetPath('icon.png')} />
    </span>
  )
}
