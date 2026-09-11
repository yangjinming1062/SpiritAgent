import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'
import * as React from 'react'

import { cn } from '../lib/utils'

// 安装器 Button：色值切到 cyber-glass 主题（var(--ui-accent) #7d9bff），保留 glass 玻璃质感。
// 主按钮用 glass + accent 边框 + accent 文字（与桌面端 hub 的"亮蓝描边玻璃钮"视觉对齐）；
// destructive / outline / ghost 与桌面端一致使用 ui-* token。
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:ring-[0.1875rem] focus-visible:ring-focus-line disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          'bg-accent/90 text-white hover:bg-accent shadow-md backdrop-blur-xs border border-accent-line/60',
        destructive:
          'bg-destructive/90 text-white hover:bg-destructive border border-destructive/60',
        outline:
          'border border-line-strong bg-glass text-text-strong hover:bg-accent-soft hover:text-accent backdrop-blur-xs',
        secondary:
          'bg-fill-faint text-text-strong hover:bg-line-standard/30 border border-line-standard',
        ghost:
          'text-text-body hover:bg-accent-soft hover:text-accent',
        link: 'text-accent underline-offset-4 decoration-accent/40 hover:underline'
      },
      size: {
        default: 'h-9 px-4 py-2 has-[>svg]:px-3',
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: 'h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5',
        lg: 'h-10 rounded-md px-6 has-[>svg]:px-4',
        icon: 'size-9',
        'icon-xs':
          "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
        'icon-sm': 'size-8',
        'icon-lg': 'size-10'
      }
    },
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
)

interface ButtonProps
  extends React.ComponentProps<'button'>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export function Button({
  className,
  variant = 'default',
  size = 'default',
  asChild = false,
  ...props
}: ButtonProps): React.JSX.Element {
  const Comp = asChild ? Slot.Root : 'button'

  return (
    <Comp
      className={cn(buttonVariants({ variant, size }), className)}
      data-size={size}
      data-slot="button"
      data-variant={variant}
      {...props}
    />
  )
}

export { buttonVariants }
