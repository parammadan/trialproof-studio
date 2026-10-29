import { AnimatePresence, motion } from 'motion/react'
import { Calendar, ChevronRight, Database, Hash, Link2, Search, Table2, Type, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api, type Column, type Dataset, type Profile, type Relationship } from '@/lib/api'
import type { WorkspaceState } from '@/lib/useWorkspace'
import { cn } from '@/lib/utils'
import Dropzone from './Dropzone'
import { Button, Dialog, Empty, Input, SectionLabel, Tip } from './ui'

const typeIcon = (t: string) => (t === 'date' || t === 'datetime' ? Calendar : t === 'numeric' || t === 'integer' ? Hash : Type)

export default function DataExplorer({ wid, ws }: { wid: string; ws: WorkspaceState }) {
  const datasets = ws.snap?.datasets ?? []
  const [profiles, setProfiles] = useState<Record<string, Profile>>({})
  const [rels, setRels] = useState<Relationship[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const key = datasets.map(d => d.sha256).join()

  useEffect(() => {
    datasets.forEach(d => api.profile(wid, d.name).then(p => setProfiles(ps => ({ ...ps, [d.name]: p }))))
    if (datasets.length > 1) api.relationships(wid).then(setRels)
    if (datasets.length && open === null) setOpen(datasets[0].name)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, key])

  const upload = async (files: File[]) => {
    setBusy(true)
    try {
      const r = await api.upload(wid, files)
      r.errors.forEach(e => toast.error(e.error))
      if (r.datasets.length) toast.success(`Imported ${r.datasets.map(d => `${d.name} (${d.nrow.toLocaleString()} rows)`).join(', ')}`)
    } catch (e) { toast.error(String((e as Error).message)) } finally { setBusy(false) }
  }
  const pilot = async () => {
    setBusy(true)
    try { await api.pilot(wid) } catch (e) { toast.error(String((e as Error).message)) } finally { setBusy(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line-soft px-3">
        <Database className="size-3.5 text-fg-3" />
        <span className="text-xs font-semibold">Data</span>
        <span className="text-[11px] text-fg-3">{datasets.length || ''}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {datasets.length === 0 ? (
          <div className="space-y-3 p-3">
            <Empty icon={<Database />} title="No datasets yet">Import analysis datasets to begin, or load the public CDISC pilot.</Empty>
            <Dropzone onFiles={upload} busy={busy} />
            <Button className="w-full" onClick={pilot} disabled={busy}>Load CDISC pilot ADSL + ADAE</Button>
          </div>
        ) : (
          <>
            <SectionLabel>Datasets</SectionLabel>
            <div className="space-y-1 px-1.5">
              {datasets.map(d => (
                <DatasetItem key={d.id} d={d} profile={profiles[d.name]} open={open === d.name}
                  onToggle={() => setOpen(o => (o === d.name ? null : d.name))} onPreview={() => setPreview(d.name)} />
              ))}
            </div>
            {rels.length > 0 && <Relationships rels={rels} />}
            <Populations datasets={datasets} profiles={profiles} />
            <div className="p-3"><Dropzone onFiles={upload} busy={busy} compact /></div>
          </>
        )}
      </div>
      {preview && <PreviewDialog wid={wid} name={preview} profile={profiles[preview]} onClose={() => setPreview(null)} />}
    </div>
  )
}

function DatasetItem({ d, profile, open, onToggle, onPreview }: { d: Dataset; profile?: Profile; open: boolean; onToggle: () => void; onPreview: () => void }) {
  const [q, setQ] = useState('')
  const cols = (profile?.columns ?? []).filter(c => !q || (c.name + ' ' + c.label).toLowerCase().includes(q.toLowerCase()))
  return (
    <div className={cn('rounded-lg border transition-colors', open ? 'border-line bg-surface-2' : 'border-transparent hover:bg-surface-2/70')}>
      <button onClick={onToggle} className="flex w-full items-center gap-2 px-2 py-2 text-left">
        <ChevronRight className={cn('size-3.5 text-fg-3 transition-transform', open && 'rotate-90')} />
        <div className="grid size-6 place-items-center rounded-md bg-accent-soft font-mono text-[9px] font-bold text-accent">{d.format.toUpperCase()}</div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[12px] font-semibold">{d.name}</div>
          <div className="text-[10.5px] text-fg-3 tabular">{d.nrow.toLocaleString()} rows · {d.ncol} cols</div>
        </div>
        <Tip content={`SHA-256 ${d.sha256} · ${d.source}`}><span className="font-mono text-[9.5px] text-fg-3">{d.sha256.slice(0, 7)}</span></Tip>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }} className="overflow-hidden">
            <div className="px-2 pb-2">
              {profile?.label && <div className="mb-1.5 px-1 text-[11px] text-fg-3">{profile.label}</div>}
              <div className="mb-1.5 flex gap-1.5">
                <div className="relative flex-1">
                  <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-fg-3" />
                  <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter columns" className="h-7 pl-6 text-xs" />
                </div>
                <Button size="sm" onClick={onPreview}><Table2 /> Preview</Button>
              </div>
              <div className="max-h-72 overflow-y-auto">
                {cols.map(c => <ColumnRow key={c.name} c={c} nrow={profile!.nrow} />)}
                {!profile && <div className="h-24 rounded tp-shimmer" />}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ColumnRow({ c, nrow }: { c: Column; nrow: number }) {
  const I = typeIcon(c.type)
  const miss = nrow ? c.n_missing / nrow : 0
  return (
    <Tip side="right" content={
      <div className="space-y-1">
        <div className="font-mono font-semibold text-fg">{c.name} <span className="font-sans font-normal text-fg-3">{c.type}</span></div>
        {c.label && <div>{c.label}</div>}
        <div className="tabular">{c.n_distinct.toLocaleString()} distinct · {c.n_missing.toLocaleString()} missing{c.min != null ? ` · ${c.min} – ${c.max}` : ''}</div>
        {c.top.length > 0 && <div className="space-y-0.5 pt-1">{c.top.slice(0, 5).map(t => (
          <div key={t.value} className="flex items-center gap-2"><span className="w-28 truncate font-mono text-[10px]">{t.value}</span>
            <span className="h-1 rounded bg-accent" style={{ width: `${Math.max(2, (60 * t.n) / nrow)}px` }} /><span className="tabular text-fg-3">{t.n}</span></div>
        ))}</div>}
      </div>
    }>
      <div className="flex cursor-default items-center gap-2 rounded px-1 py-[3px] hover:bg-surface-3/60">
        <I className="size-3 shrink-0 text-fg-3" />
        <span className="w-[76px] shrink-0 truncate font-mono text-[11px]">{c.name}</span>
        <span className="min-w-0 flex-1 truncate text-[10.5px] text-fg-3">{c.label}</span>
        {miss > 0 && <span className="h-1 w-6 shrink-0 overflow-hidden rounded bg-surface-3"><span className="block h-full bg-warn" style={{ width: `${Math.max(8, miss * 100)}%` }} /></span>}
      </div>
    </Tip>
  )
}

function Relationships({ rels }: { rels: Relationship[] }) {
  const keys = rels.flatMap(r => r.keys)
  if (!keys.length) return null
  return (
    <>
      <SectionLabel>Relationships</SectionLabel>
      <div className="space-y-1.5 px-3">
        {keys.map(k => (
          <div key={k.parent + k.child + k.column} className="rounded-lg border border-line-soft bg-surface-2/60 px-2.5 py-2">
            <div className="flex items-center gap-1.5 font-mono text-[11px]">
              <span className="font-semibold">{k.child}</span><Link2 className="size-3 text-accent" /><span className="font-semibold">{k.parent}</span>
              <span className="ml-auto text-fg-3">via {k.column}</span>
            </div>
            <div className="mt-1.5 flex h-1.5 overflow-hidden rounded bg-surface-3">
              <div className="bg-ok" style={{ width: `${(100 * k.matched) / Math.max(1, k.child_distinct)}%` }} />
            </div>
            <div className="mt-1 text-[10.5px] leading-snug text-fg-3 tabular">
              {k.matched.toLocaleString()} of {k.child_distinct.toLocaleString()} {k.child} keys found in {k.parent}
              {k.parent_without_child > 0 && <> · {k.parent_without_child.toLocaleString()} {k.parent} rows with no {k.child} record</>}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

function Populations({ datasets, profiles }: { datasets: Dataset[]; profiles: Record<string, Profile> }) {
  // Subject-level dataset: the one whose USUBJID is unique. Population flags: Y/N columns ending in FL.
  const subj = useMemo(() => datasets.find(d => {
    const c = profiles[d.name]?.columns.find(x => x.name === 'USUBJID')
    return c && c.n_distinct === profiles[d.name].nrow
  }), [datasets, profiles])
  if (!subj) return null
  const p = profiles[subj.name]
  const flags = p.columns.filter(c => /FL$/.test(c.name) && c.top.some(t => t.value === 'Y'))
  if (!flags.length) return null
  return (
    <>
      <SectionLabel right={<span className="font-mono normal-case tracking-normal">{subj.name}</span>}>Populations & flags</SectionLabel>
      <div className="space-y-0.5 px-3">
        {flags.map(f => {
          const y = f.top.find(t => t.value === 'Y')?.n ?? 0
          return (
            <Tip key={f.name} side="right" content={f.label || f.name}>
              <div className="flex items-center gap-2 py-[3px]">
                <Users className="size-3 text-fg-3" />
                <span className="w-20 font-mono text-[11px]">{f.name}</span>
                <span className="h-1.5 flex-1 overflow-hidden rounded bg-surface-3"><span className="block h-full bg-accent/80" style={{ width: `${(100 * y) / p.nrow}%` }} /></span>
                <span className="w-14 text-right text-[10.5px] text-fg-3 tabular">{y}/{p.nrow}</span>
              </div>
            </Tip>
          )
        })}
      </div>
    </>
  )
}

function PreviewDialog({ wid, name, profile, onClose }: { wid: string; name: string; profile?: Profile; onClose: () => void }) {
  const [page, setPage] = useState(0)
  const [data, setData] = useState<{ total: number; rows: Record<string, string>[] } | null>(null)
  const [filter, setFilter] = useState('')
  const limit = 100
  useEffect(() => { setData(null); api.rows(wid, name, page * limit, limit).then(setData) }, [wid, name, page])
  const cols = profile?.columns.map(c => c.name) ?? Object.keys(data?.rows[0] ?? {})
  const shown = (data?.rows ?? []).filter(r => !filter || Object.values(r).some(v => v.toLowerCase().includes(filter.toLowerCase())))
  return (
    <Dialog open onOpenChange={o => !o && onClose()} wide title={<span className="font-mono">{name}</span>}
      description={profile ? `${profile.nrow.toLocaleString()} rows · ${profile.ncol} columns${profile.label ? ' · ' + profile.label : ''}` : undefined}>
      <div className="flex items-center gap-2 border-b border-line-soft px-5 py-2.5">
        <div className="relative w-64"><Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-fg-3" />
          <Input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter this page" className="h-7 pl-6 text-xs" /></div>
        <div className="ml-auto flex items-center gap-2 text-xs text-fg-3 tabular">
          {data && <span>rows {page * limit + 1}–{Math.min(data.total, (page + 1) * limit)} of {data.total.toLocaleString()}</span>}
          <Button size="xs" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Prev</Button>
          <Button size="xs" disabled={!data || (page + 1) * limit >= data.total} onClick={() => setPage(p => p + 1)}>Next</Button>
        </div>
      </div>
      <div className="max-h-[66vh] overflow-auto">
        <table className="w-max min-w-full border-separate border-spacing-0 font-mono text-[11px]">
          <thead className="sticky top-0 z-10 bg-surface-2">
            <tr>{cols.map(c => {
              const col = profile?.columns.find(x => x.name === c)
              return (
                <th key={c} className="border-b border-line px-2.5 py-1.5 text-left align-bottom font-semibold">
                  <div>{c}</div>
                  {col && <div className="font-sans text-[9.5px] font-normal text-fg-3">{col.type}{col.n_missing ? ` · ${col.n_missing} missing` : ''}</div>}
                </th>
              )
            })}</tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} className="hover:bg-surface-3/40">
                {cols.map(c => <td key={c} className={cn('max-w-64 truncate border-b border-line-soft px-2.5 py-1', r[c] === '' && 'text-fg-3/50')}>{r[c] === '' ? '·' : r[c]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {!data && <div className="m-5 h-40 rounded tp-shimmer" />}
      </div>
    </Dialog>
  )
}

