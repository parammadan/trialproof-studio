import { motion } from 'motion/react'
import { FileUp } from 'lucide-react'
import { useRef, useState } from 'react'
import { cn } from '@/lib/utils'

const OK = /\.(csv|xpt|rds)$/i

export default function Dropzone({ onFiles, busy, compact, className }: { onFiles: (f: File[]) => void; busy?: boolean; compact?: boolean; className?: string }) {
  const [over, setOver] = useState(false)
  const [rejected, setRejected] = useState<string[]>([])
  const input = useRef<HTMLInputElement>(null)

  const take = (list: FileList | null) => {
    if (!list) return
    const files = Array.from(list)
    const good = files.filter(f => OK.test(f.name))
    setRejected(files.filter(f => !OK.test(f.name)).map(f => f.name))
    if (good.length) onFiles(good)
  }

  return (
    <div className={className}>
      <motion.button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={e => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); take(e.dataTransfer.files) }}
        animate={{ scale: over ? 1.01 : 1 }}
        disabled={busy}
        className={cn(
          'group relative flex w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-xl border border-dashed text-center transition-colors outline-none focus-visible:ring-2 focus-visible:ring-accent/50',
          compact ? 'px-3 py-4' : 'px-6 py-10',
          over ? 'border-accent bg-accent-soft' : 'border-line hover:border-fg-3/60 hover:bg-surface-2/60',
          busy && 'cursor-progress',
        )}
      >
        {busy && <div className="tp-shimmer absolute inset-0" />}
        <div className={cn('grid place-items-center rounded-lg border border-line bg-surface-2 text-fg-2 transition group-hover:text-accent', compact ? 'size-7' : 'size-10')}>
          <FileUp className={compact ? 'size-3.5' : 'size-4.5'} />
        </div>
        <div className={cn('font-medium text-fg-2', compact ? 'text-xs' : 'text-sm')}>
          {busy ? 'Parsing in R…' : over ? 'Drop to import' : compact ? 'Add datasets' : 'Drop datasets here, or browse'}
        </div>
        {!compact && <div className="text-xs text-fg-3">CSV, SAS transport (.xpt) or R (.rds) · parsed by R with haven/readr</div>}
      </motion.button>
      <input ref={input} type="file" multiple accept=".csv,.xpt,.rds" className="hidden" onChange={e => { take(e.target.files); e.target.value = '' }} />
      {rejected.length > 0 && <div className="mt-2 text-[11px] text-bad">Skipped {rejected.join(', ')}: only .csv, .xpt and .rds are supported.</div>}
    </div>
  )
}
