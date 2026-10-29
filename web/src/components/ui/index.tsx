import * as RDialog from '@radix-ui/react-dialog'
import * as RPopover from '@radix-ui/react-popover'
import * as RTabs from '@radix-ui/react-tabs'
import * as RTooltip from '@radix-ui/react-tooltip'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { X } from 'lucide-react'
import * as React from 'react'
import { cn } from '@/lib/utils'

const button = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-[background,color,box-shadow,transform] duration-150 select-none outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:pointer-events-none disabled:opacity-45 active:scale-[0.98] [&_svg]:size-3.5 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-fg hover:brightness-110 shadow-[0_0_0_1px_oklch(1_0_0/0.08)_inset,0_6px_16px_-8px_var(--accent)]',
        secondary: 'bg-surface-3 text-fg hover:bg-[color-mix(in_oklch,var(--surface-3),var(--fg)_7%)] border border-line',
        ghost: 'text-fg-2 hover:text-fg hover:bg-surface-3',
        outline: 'border border-line text-fg-2 hover:text-fg hover:bg-surface-3',
        danger: 'bg-bad-soft text-bad hover:bg-bad/20 border border-bad/30',
        ok: 'bg-ok-soft text-ok hover:bg-ok/20 border border-ok/30',
      },
      size: { xs: 'h-6 px-2 text-[11px]', sm: 'h-7 px-2.5 text-xs', md: 'h-8 px-3 text-[13px]', lg: 'h-10 px-4 text-sm', icon: 'size-7', iconsm: 'size-6' },
    },
    defaultVariants: { variant: 'secondary', size: 'sm' },
  },
)
export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button> & { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...p }, ref) => {
  const C = asChild ? Slot : 'button'
  return <C ref={ref} className={cn(button({ variant, size }), className)} {...p} />
})

const badge = cva('inline-flex items-center gap-1 rounded-full px-2 h-5 text-[10.5px] font-medium tracking-wide whitespace-nowrap [&_svg]:size-3', {
  variants: {
    tone: {
      neutral: 'bg-surface-3 text-fg-2', accent: 'bg-accent-soft text-accent', ok: 'bg-ok-soft text-ok', bad: 'bg-bad-soft text-bad',
      warn: 'bg-warn-soft text-warn', violet: 'bg-violet-soft text-violet', outline: 'border border-line text-fg-3',
    },
  },
  defaultVariants: { tone: 'neutral' },
})
export const Badge = ({ className, tone, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) =>
  <span className={cn(badge({ tone }), className)} {...p} />

export const Kbd = ({ children, className }: { children: React.ReactNode; className?: string }) =>
  <kbd className={cn('inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-line bg-surface-2 px-1 font-mono text-[10px] text-fg-3', className)}>{children}</kbd>

export const TooltipProvider = RTooltip.Provider
export function Tip({ content, children, side = 'top' }: { content: React.ReactNode; children: React.ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  return (
    <RTooltip.Root delayDuration={300}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content side={side} sideOffset={6} className="z-50 max-w-xs rounded-md border border-line bg-surface-2 px-2 py-1 text-[11px] text-fg-2 shadow-panel backdrop-blur-xl data-[state=delayed-open]:animate-in">
          {content}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  )
}

export const Tabs = RTabs.Root
export const TabsContent = ({ className, ...p }: RTabs.TabsContentProps) => <RTabs.Content className={cn('outline-none data-[state=inactive]:hidden', className)} {...p} />
export const TabsList = ({ className, ...p }: RTabs.TabsListProps) => <RTabs.List className={cn('flex items-center gap-0.5', className)} {...p} />
export const TabsTrigger = ({ className, ...p }: RTabs.TabsTriggerProps) => (
  <RTabs.Trigger
    className={cn(
      'relative inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-fg-3 transition-colors hover:text-fg-2 outline-none',
      'data-[state=active]:bg-surface-3 data-[state=active]:text-fg [&_svg]:size-3.5', className)}
    {...p}
  />
)

export function Dialog({ open, onOpenChange, title, description, children, className, wide }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: React.ReactNode; description?: React.ReactNode; children: React.ReactNode; className?: string; wide?: boolean
}) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-40 bg-black/45 backdrop-blur-[2px]" />
        <RDialog.Content className={cn('fixed left-1/2 top-1/2 z-50 flex max-h-[88vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-line bg-surface-2 shadow-panel backdrop-blur-2xl outline-none', wide ? 'max-w-6xl' : 'max-w-lg', className)}>
          <div className="flex items-start justify-between gap-4 border-b border-line-soft px-5 py-4">
            <div>
              <RDialog.Title className="text-sm font-semibold text-fg">{title}</RDialog.Title>
              {description && <RDialog.Description className="mt-1 text-xs text-fg-3">{description}</RDialog.Description>}
            </div>
            <RDialog.Close asChild><Button variant="ghost" size="iconsm" aria-label="Close"><X /></Button></RDialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">{children}</div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  )
}

export const Popover = RPopover.Root
export const PopoverTrigger = RPopover.Trigger
export const PopoverAnchor = RPopover.Anchor
export const PopoverContent = ({ className, ...p }: RPopover.PopoverContentProps) => (
  <RPopover.Portal>
    <RPopover.Content sideOffset={6} collisionPadding={12} className={cn('z-50 w-80 rounded-lg border border-line bg-surface-2 p-3 shadow-panel backdrop-blur-2xl outline-none', className)} {...p} />
  </RPopover.Portal>
)

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn('h-8 w-full rounded-md border border-line bg-bg px-2.5 text-[13px] text-fg placeholder:text-fg-3 outline-none transition focus:border-accent/60 focus:ring-2 focus:ring-accent/20', className)} {...p} />
))

export const Spinner = ({ className }: { className?: string }) => (
  <svg className={cn('size-3.5 animate-spin', className)} viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".2" strokeWidth="3" /><path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" /></svg>
)

export const SectionLabel = ({ children, right, className }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) => (
  <div className={cn('flex items-center justify-between px-3 pb-1.5 pt-3 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-3', className)}>
    <span>{children}</span>{right}
  </div>
)

export function Empty({ icon, title, children, className }: { icon?: React.ReactNode; title: string; children?: React.ReactNode; className?: string }) {
  return (
    <div className={cn('flex h-full flex-col items-center justify-center gap-2 p-8 text-center', className)}>
      {icon && <div className="mb-1 grid size-10 place-items-center rounded-xl border border-line bg-surface-2 text-fg-3 [&_svg]:size-4.5">{icon}</div>}
      <div className="text-[13px] font-medium text-fg-2">{title}</div>
      {children && <div className="max-w-xs text-xs leading-relaxed text-fg-3">{children}</div>}
    </div>
  )
}
