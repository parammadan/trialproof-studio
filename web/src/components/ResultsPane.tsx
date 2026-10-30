import { motion } from 'motion/react'
import {
  ArrowDownWideNarrow, BarChart3, Bot, ChevronRight, CircleCheck, CircleDashed, CircleX, Code2, FileWarning, Fingerprint,
  MessageSquare, Search, ShieldCheck, Table2, Target,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api, type Cell, type Table, type TableValidation } from '@/lib/api'
import type { WorkspaceState } from '@/lib/useWorkspace'
import { ago, cellKey, cn, fmtPct, pct } from '@/lib/utils'
import type { CellRef, RightTab } from './Workspace'
import { Badge, Button, Empty, Input, Popover, PopoverAnchor, PopoverContent, Tabs, TabsContent, TabsList, TabsTrigger, Tip } from './ui'

type Send = (t: string, c?: Record<string, unknown>) => void
type Row = Record<string, any>

// Categorical series colors (validated for CVD separation on both surfaces); text stays in text tokens.
const SERIES_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500']
const SERIES_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']
const series = (i: number) => (document.documentElement.classList.contains('light') ? SERIES_LIGHT : SERIES_DARK)[i % 4]

export default function ResultsPane({ wid, ws, tab, setTab, cell, setCell, onTrace, send, onReveal }: {
  wid: string; ws: WorkspaceState; tab: RightTab; setTab: (t: RightTab) => void; cell: CellRef | null; setCell: (c: CellRef | null) => void
  onTrace: (c: CellRef) => void; send: Send; onReveal: (a: number, b: number, n: string) => void
}) {
  const run = ws.snap?.run
  const val = ws.snap?.validation
  const totals = (val?.tables ?? []).reduce((a, t) => ({ p: a.p + t.passed, m: a.m + t.mismatch, nv: a.nv + (t.status === 'not_verified' ? 1 : 0) }), { p: 0, m: 0, nv: 0 })
  return (
    <Tabs value={tab} onValueChange={v => setTab(v as RightTab)} className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line-soft px-2">
        <TabsList>
          <TabsTrigger value="results"><Table2 /> Results</TabsTrigger>
          <TabsTrigger value="verify"><ShieldCheck /> Verification
            {val && (totals.m > 0 ? <Badge tone="bad" className="h-4 px-1.5">{totals.m}</Badge> : totals.p > 0 ? <CircleCheck className="!size-3 text-ok" /> : null)}
          </TabsTrigger>
          <TabsTrigger value="lineage"><Fingerprint /> Lineage</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="results" className="min-h-0 flex-1 overflow-y-auto">
        {!run ? <Empty icon={<Table2 />} title="No results yet">Results appear here when a program runs. Every number comes from an actual R execution.</Empty>
          : <Results wid={wid} ws={ws} cell={cell} setCell={setCell} onTrace={onTrace} send={send} onReveal={onReveal} />}
      </TabsContent>
      <TabsContent value="verify" className="min-h-0 flex-1 overflow-y-auto"><Verification wid={wid} ws={ws} onTrace={onTrace} send={send} /></TabsContent>
      <TabsContent value="lineage" className="min-h-0 flex-1 overflow-y-auto"><Lineage ws={ws} send={send} onReveal={onReveal} /></TabsContent>
    </Tabs>
  )
}

/* ---------------- results ---------------- */

function Results({ ws, cell, setCell, onTrace, send, onReveal }: {
  wid: string; ws: WorkspaceState; cell: CellRef | null; setCell: (c: CellRef | null) => void; onTrace: (c: CellRef) => void; send: Send; onReveal: (a: number, b: number, n: string) => void
}) {
  const run = ws.snap!.run!
  const [tid, setTid] = useState<string | null>(null)
  const table = run.tables.find(t => t.id === tid) ?? run.tables[0]
  const tv = ws.snap?.validation?.tables.find(t => t.table_id === table?.id)
  const prev = ws.prevRun?.tables.find(t => t.id === table?.id)
  const err = run.status !== 'succeeded' ? (run.result as { message?: string; line?: number; end_line?: number; kind?: string }) : null

  return (
    <div className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-fg-3">
        <Badge tone={run.status === 'succeeded' ? 'ok' : 'bad'}>{run.status}</Badge>
        <span>program v{run.program_version}</span><span>·</span><span>{ago(run.ended)}</span><span>·</span>
        <Tip content={<div className="space-y-0.5 font-mono text-[10px]"><div>program {run.provenance.program_sha256}</div>{Object.entries(run.provenance.datasets).map(([k, v]) => <div key={k}>{k} {v}</div>)}<div>{run.provenance.r_version}</div></div>}>
          <span className="cursor-help underline decoration-dotted underline-offset-2">provenance</span>
        </Tip>
        <span>·</span><span>{run.provenance.isolation ?? (run.provenance.backend === 'sandbox' ? 'macOS sandbox, no network' : run.provenance.backend)}</span>
      </div>

      {err && (
        <div className="rounded-xl border border-bad/30 bg-bad-soft/50 p-3">
          <div className="flex items-center gap-2 text-[12px] font-semibold text-bad"><FileWarning className="size-3.5" /> {err.kind === 'parse' ? 'Parse error' : err.kind === 'runtime' ? 'Runtime error' : `Run ${run.status}`}{err.line ? ` · line ${err.line}${err.end_line && err.end_line !== err.line ? `–${err.end_line}` : ''}` : ''}</div>
          <pre className="mt-1.5 whitespace-pre-wrap font-mono text-[11.5px] text-fg-2">{err.message}</pre>
          <div className="mt-2 flex gap-1.5">
            {err.line && <Button size="xs" onClick={() => onReveal(err.line!, err.end_line ?? err.line!, err.message ?? '')}><Code2 /> Show in editor</Button>}
            <Button size="xs" variant="primary" onClick={() => send('The last run failed. Read the error, fix the program and rerun it.')}><Bot /> Ask the agent to fix it</Button>
          </div>
        </div>
      )}

      {run.tables.length > 1 && (
        <div className="flex flex-wrap gap-1">{run.tables.map(t => <button key={t.id} onClick={() => setTid(t.id)} className={cn('rounded-md px-2 py-1 text-[11px]', t.id === table.id ? 'bg-surface-3 text-fg' : 'text-fg-3 hover:text-fg-2')}>{t.title || t.id}</button>)}</div>
      )}
      {!table && !err && <Empty icon={<Table2 />} title="The run emitted no tables">Use <code className="font-mono">tp_emit_table()</code> to publish a result.</Empty>}
      {table && (table.type === 'teae_summary'
        ? <Teae key={run.id + table.id} table={table} prev={prev} tv={tv} cell={cell} setCell={setCell} onTrace={onTrace} send={send} />
        : <Generic table={table} />)}
    </div>
  )
}

type Node = { key: string; level: string; soc: string | null; pt: string | null; label: string; cells: Record<string, Row>; total: number; children: Node[] }

function tree(rows: Row[], groups: string[]): Node {
  const norm = (v: unknown) => (v == null || v === '' || v === 'NA' ? null : String(v))
  const mk = (level: string, soc: string | null, pt: string | null, label: string): Node => ({ key: cellKey({ level, soc, pt, group: '' }), level, soc, pt, label, cells: {}, total: 0, children: [] })
  const root = mk('any', null, null, 'Subjects with ≥1 TEAE')
  const socs = new Map<string, Node>()
  for (const r of rows) {
    const lvl = String(r.level), soc = norm(r.soc), pt = norm(r.pt), g = String(r.group)
    let node: Node
    if (lvl === 'any') node = root
    else {
      let s = socs.get(soc ?? '')
      if (!s) { s = mk('soc', soc, null, soc ?? '(missing SOC)'); socs.set(soc ?? '', s) }
      if (lvl === 'soc') node = s
      else {
        let p = s.children.find(c => c.pt === pt)
        if (!p) { p = mk('pt', soc, pt, pt ?? '(missing PT)'); s.children.push(p) }
        node = p
      }
    }
    node.cells[g] = r
    node.total += Number(r.n) || 0
  }
  root.children = [...socs.values()]
  void groups
  return root
}

function Teae({ table, prev, tv, cell, setCell, onTrace, send }: {
  table: Table; prev?: Table; tv?: TableValidation; cell: CellRef | null; setCell: (c: CellRef | null) => void; onTrace: (c: CellRef) => void; send: Send
}) {
  const groups = useMemo(() => [...new Set(table.rows.map(r => String(r.group)))].sort((a, b) => (a === 'Total' ? 1 : b === 'Total' ? -1 : a.localeCompare(b))), [table])
  const root = useMemo(() => tree(table.rows, groups), [table, groups])
  const [sort, setSort] = useState<'freq' | 'alpha'>('freq')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [only, setOnly] = useState<'all' | 'mismatch' | 'changed'>('all')
  const [view, setView] = useState<'table' | 'chart'>('table')

  const status = useMemo(() => {
    const m = new Map<string, Cell>()
    tv?.cells.forEach(c => m.set(cellKey(c), c))
    return m
  }, [tv])
  const before = useMemo(() => {
    if (!prev) return null
    const m = new Map<string, Row>()
    prev.rows.forEach(r => m.set(cellKey({ level: String(r.level), soc: r.soc as string | null, pt: r.pt as string | null, group: String(r.group) }), r))
    return m
  }, [prev])
  const changedOf = (n: Node, g: string) => {
    if (!before) return null
    const r = n.cells[g]
    const b = before.get(cellKey({ level: n.level, soc: n.soc, pt: n.pt, group: g }))
    if (!r && !b) return null
    if (!b) return { was: null }
    if (!r || Number(r.n) !== Number(b.n) || Number(r.N) !== Number(b.N)) return { was: b }
    return null
  }
  const nChanged = useMemo(() => {
    if (!before) return 0
    let k = 0
    const walk = (n: Node) => { groups.forEach(g => { if (changedOf(n, g)) k++ }); n.children.forEach(walk) }
    walk(root)
    return k
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [before, root, groups])

  const order = (ns: Node[]) => [...ns].sort((a, b) => (sort === 'alpha' ? a.label.localeCompare(b.label) : b.total - a.total || a.label.localeCompare(b.label)))
  const match = (n: Node) => !q || n.label.toLowerCase().includes(q.toLowerCase())
  const flagged = (n: Node) => groups.some(g => only === 'mismatch' ? status.get(cellKey({ level: n.level, soc: n.soc, pt: n.pt, group: g }))?.status === 'mismatch' : changedOf(n, g))
  const visibleSoc = order(root.children).filter(s => (match(s) || s.children.some(match)) && (only === 'all' || flagged(s) || s.children.some(flagged)))
  const anyRow = root.cells

  return (
    <div className="space-y-3">
      {/* summary cards: real values from the 'any' rows */}
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(groups.length, 4)}, minmax(0, 1fr))` }}>
        {groups.map((g, i) => {
          const r = anyRow[g]
          const p = pct(Number(r?.n), Number(r?.N))
          return (
            <div key={g} className="rounded-xl border border-line bg-surface-2 px-3 py-2.5">
              <div className="flex items-center gap-1.5 truncate text-[10.5px] text-fg-3"><span className="size-2 shrink-0 rounded-sm" style={{ background: series(i) }} /><span className="truncate">{g}</span></div>
              <div className="mt-1 flex items-baseline gap-1.5">
                <span className="text-[20px] font-semibold tabular tracking-tight">{fmtPct(p)}<span className="text-[12px] text-fg-3">%</span></span>
              </div>
              <div className="text-[10.5px] text-fg-3 tabular">{r ? `${r.n} of N=${r.N} with ≥1 TEAE` : 'no rows'}</div>
            </div>
          )
        })}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <div className="relative min-w-36 flex-1"><Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-fg-3" /><Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter SOC or PT" className="h-7 pl-6 text-xs" /></div>
        <Seg value={sort} on={setSort} opts={[['freq', <><ArrowDownWideNarrow className="size-3" /> Frequency</>], ['alpha', 'A–Z']]} />
        <Seg value={only} on={setOnly} opts={[['all', 'All'], ['mismatch', `Mismatches${tv?.mismatch ? ` ${tv.mismatch}` : ''}`], ...(before ? [['changed', `Changed ${nChanged}`] as [typeof only, string]] : [])]} />
        <Seg value={view} on={setView} opts={[['table', <Table2 className="size-3" />], ['chart', <BarChart3 className="size-3" />]]} />
      </div>
      {before && nChanged > 0 && <div className="flex items-center gap-1.5 text-[11px] text-violet"><span className="size-2 rounded-sm ring-1 ring-violet" /> {nChanged} cell{nChanged === 1 ? '' : 's'} changed since the previous successful run; click a cell for the old value</div>}

      {view === 'chart' ? <Chart root={root} groups={groups} /> : (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full min-w-[380px] border-separate border-spacing-0 text-[12px]">
            <thead>
              <tr className="bg-surface-2">
                <th className="sticky left-0 z-[1] border-b border-line bg-surface-2 px-3 py-2 text-left text-[11px] font-medium text-fg-3">SOC / Preferred term</th>
                {groups.map((g, i) => (
                  <th key={g} className="border-b border-line px-1.5 py-2 text-right align-bottom text-[11px] font-medium leading-tight text-fg-2">
                    <div className="flex items-start justify-end gap-1.5"><span className="mt-1 size-2 shrink-0 rounded-sm" style={{ background: series(i) }} /><span className="max-w-[92px] text-right">{g}</span></div>
                    <div className="font-normal text-fg-3 tabular">N={String(anyRow[g]?.N ?? '–')}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <TRow n={root} groups={groups} depth={0} status={status} changedOf={changedOf} cell={cell} setCell={setCell} onTrace={onTrace} send={send} tableId={table.id} />
              {visibleSoc.map(s => {
                const isOpen = open.has(s.key) || !!q || only !== 'all'
                return [
                  <TRow key={s.key} n={s} groups={groups} depth={0} status={status} changedOf={changedOf} cell={cell} setCell={setCell} onTrace={onTrace} send={send} tableId={table.id}
                    expandable={s.children.length > 0} open={isOpen} onToggle={() => setOpen(o => { const x = new Set(o); if (x.has(s.key)) x.delete(s.key); else x.add(s.key); return x })} />,
                  ...(isOpen ? order(s.children).filter(c => (match(c) || match(s)) && (only === 'all' || flagged(c))).map(c =>
                    <TRow key={c.key} n={c} groups={groups} depth={1} status={status} changedOf={changedOf} cell={cell} setCell={setCell} onTrace={onTrace} send={send} tableId={table.id} />) : []),
                ]
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex items-center justify-between text-[10.5px] text-fg-3">
        <span>n (%) of subjects · a subject is counted once per row · click a value to trace it</span>
        <button className="text-accent hover:underline" onClick={() => setOpen(o => (o.size ? new Set() : new Set(root.children.map(c => c.key))))}>{open.size ? 'Collapse all' : 'Expand all'}</button>
      </div>
    </div>
  )
}

function Seg<T extends string>({ value, on, opts }: { value: T; on: (v: T) => void; opts: [T, React.ReactNode][] }) {
  return (
    <div className="flex h-7 items-center rounded-md border border-line p-0.5">
      {opts.map(([v, l]) => <button key={v} onClick={() => on(v)} className={cn('flex h-full items-center gap-1 rounded px-2 text-[11px] transition', value === v ? 'bg-surface-3 text-fg' : 'text-fg-3 hover:text-fg-2')}>{l}</button>)}
    </div>
  )
}

function TRow({ n, groups, depth, status, changedOf, cell, setCell, onTrace, send, tableId, expandable, open, onToggle }: {
  n: Node; groups: string[]; depth: number; status: Map<string, Cell>; changedOf: (n: Node, g: string) => { was: Row | null } | null
  cell: CellRef | null; setCell: (c: CellRef | null) => void; onTrace: (c: CellRef) => void; send: Send; tableId: string
  expandable?: boolean; open?: boolean; onToggle?: () => void
}) {
  const head = n.level === 'any'
  return (
    <tr className={cn('group', head ? 'bg-accent-soft/30' : n.level === 'soc' ? '' : 'bg-bg/30')}>
      <td className={cn('sticky left-0 z-[1] max-w-[150px] border-b border-line-soft bg-surface px-3 py-1.5', head && 'bg-[color-mix(in_oklch,var(--surface),var(--accent)_6%)]')}>
        <div className="flex items-center gap-1" style={{ paddingLeft: depth * 16 }}>
          {expandable ? <button onClick={onToggle} className="text-fg-3 hover:text-fg"><ChevronRight className={cn('size-3 transition-transform', open && 'rotate-90')} /></button> : <span className="w-3" />}
          <span className={cn('truncate', head ? 'font-semibold' : n.level === 'soc' ? 'font-medium text-fg' : 'text-fg-2')} title={n.label}>{n.label}</span>
          {expandable && <span className="ml-1 text-[10px] text-fg-3">{n.children.length}</span>}
        </div>
      </td>
      {groups.map(g => {
        const r = n.cells[g]
        const ref: CellRef = { table_id: tableId, level: n.level, soc: n.soc, pt: n.pt, group: g, n: r?.n ?? 0, N: r?.N }
        const st = status.get(cellKey(ref))
        const ch = changedOf(n, g)
        const sel = cell && cellKey(cell) === cellKey(ref) && cell.table_id === tableId
        const p = pct(Number(r?.n ?? 0), Number(r?.N))
        return (
          <td key={g} className="border-b border-line-soft px-1 py-0.5 text-right">
            <CellPop open={!!sel} onOpenChange={o => setCell(o ? { ...ref, status: st?.status } : null)} cellRef={ref} r={r} st={st} was={ch?.was} onTrace={onTrace} send={send}>
              <button
                onClick={() => setCell(sel ? null : { ...ref, status: st?.status })}
                style={ch ? { animation: 'tp-flash 2.4s ease-out' } : undefined}
                className={cn('inline-flex w-full items-center justify-end gap-1 rounded-md px-1.5 py-1 font-mono text-[11px] tabular transition hover:bg-surface-3',
                  sel && 'bg-accent-soft ring-1 ring-accent/60', ch && 'ring-1 ring-violet/70', st?.status === 'mismatch' && 'bg-bad-soft text-bad', !r && 'text-fg-3/60')}>
                <span>{r ? r.n : 0}</span>
                <span className="w-[42px] text-left text-fg-3">{r ? `(${fmtPct(p)})` : ''}</span>
                <StatusDot s={st?.status} />
              </button>
            </CellPop>
          </td>
        )
      })}
    </tr>
  )
}

const StatusDot = ({ s }: { s?: Cell['status'] }) =>
  s === 'passed' ? <CircleCheck className="size-3 shrink-0 text-ok opacity-70" /> : s === 'mismatch' ? <CircleX className="size-3 shrink-0 text-bad" /> : <CircleDashed className="size-3 shrink-0 text-fg-3/50" />

function CellPop({ children, open, onOpenChange, cellRef, r, st, was, onTrace, send }: {
  children: React.ReactNode; open: boolean; onOpenChange: (o: boolean) => void; cellRef: CellRef; r?: Row; st?: Cell; was?: Row | null
  onTrace: (c: CellRef) => void; send: Send
}) {
  const label = cellRef.level === 'any' ? 'Subjects with ≥1 TEAE' : cellRef.level === 'soc' ? cellRef.soc : cellRef.pt
  const ctx = { selected_cell: { ...cellRef, value_n: r?.n ?? 0, value_N: r?.N } }
  const ask = (t: string) => { send(t, ctx); onOpenChange(false) }
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverAnchor asChild>{children}</PopoverAnchor>
      <PopoverContent side="left" align="start" className="w-80" onOpenAutoFocus={e => e.preventDefault()}>
        <div className="text-[11px] text-fg-3">{cellRef.group}</div>
        <div className="mb-2 text-[12.5px] font-semibold leading-snug">{label}</div>
        <div className="mb-2 grid grid-cols-3 gap-1.5 text-center">
          <Stat k="n" v={String(r?.n ?? 0)} /><Stat k="N" v={String(r?.N ?? '–')} /><Stat k="%" v={fmtPct(pct(Number(r?.n ?? 0), Number(r?.N)))} />
        </div>
        <div className={cn('mb-2 flex items-start gap-1.5 rounded-md px-2 py-1.5 text-[11px]', st?.status === 'passed' ? 'bg-ok-soft text-ok' : st?.status === 'mismatch' ? 'bg-bad-soft text-bad' : 'bg-surface-3 text-fg-3')}>
          <StatusDot s={st?.status} />
          <span>{st?.status === 'passed' ? `Matches the independent reference (n=${st.ref_n}, N=${st.ref_N})`
            : st?.status === 'mismatch' ? `Mismatch: reference n=${st.ref_n ?? '–'}, N=${st.ref_N ?? '–'}${st.reason ? ` · ${st.reason}` : ''}`
              : 'Not verified for this cell'}</span>
        </div>
        {was !== undefined && <div className="mb-2 rounded-md bg-violet-soft px-2 py-1.5 text-[11px] text-violet">{was ? `Changed since the previous run: was ${was.n} / ${was.N}` : 'New since the previous run'}</div>}
        <div className="grid gap-1">
          <Button size="sm" variant="primary" onClick={() => { onTrace(cellRef); onOpenChange(false) }}><Fingerprint /> Trace lineage (deterministic)</Button>
          <Button size="sm" onClick={() => ask('Where did this number come from? Trace it and show the R code responsible.')}><MessageSquare /> Ask the agent where it came from</Button>
          <Button size="sm" variant="ghost" onClick={() => ask('Highlight the R code responsible for this value.')}><Code2 /> Show the responsible code</Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

const Stat = ({ k, v }: { k: string; v: string }) => (
  <div className="rounded-md border border-line-soft bg-bg/50 py-1.5"><div className="text-[9.5px] uppercase tracking-wider text-fg-3">{k}</div><div className="font-mono text-[14px] font-semibold tabular">{v}</div></div>
)

function Chart({ root, groups }: { root: Node; groups: string[] }) {
  const [hover, setHover] = useState<string | null>(null)
  const pts = root.children.flatMap(s => s.children)
  const pc = (n: Node, g: string) => pct(Number(n.cells[g]?.n ?? 0), Number(n.cells[g]?.N ?? root.cells[g]?.N)) ?? 0
  const top = [...pts].sort((a, b) => Math.max(...groups.map(g => pc(b, g))) - Math.max(...groups.map(g => pc(a, g)))).slice(0, 12)
  const max = Math.max(1, ...top.flatMap(n => groups.map(g => pc(n, g))))
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="mb-1 text-[12px] font-semibold">Most frequent preferred terms</div>
      <div className="mb-3 text-[10.5px] text-fg-3">% of subjects in each group with the event. Descriptive only: no test of differences was run.</div>
      <div className="mb-3 flex flex-wrap gap-3">{groups.map((g, i) => <span key={g} className="flex items-center gap-1.5 text-[11px] text-fg-2"><span className="size-2.5 rounded-sm" style={{ background: series(i) }} />{g}</span>)}</div>
      <div className="space-y-2.5">
        {top.map(n => (
          <div key={n.key} className="grid grid-cols-[minmax(90px,38%)_1fr] items-center gap-3" onMouseEnter={() => setHover(n.key)} onMouseLeave={() => setHover(null)}>
            <div className="truncate text-right text-[11px] text-fg-2" title={n.label}>{n.label}</div>
            <div className="relative space-y-[2px]">
              {groups.map((g, i) => {
                const v = pc(n, g)
                return (
                  <div key={g} className="flex items-center gap-1.5">
                    <motion.div initial={{ width: 0 }} animate={{ width: `${(v / max) * 82}%` }} transition={{ duration: 0.5, delay: i * 0.04 }}
                      className="h-[6px] rounded-r-[4px]" style={{ background: series(i), opacity: hover && hover !== n.key ? 0.35 : 1 }} />
                    {hover === n.key && <span className="font-mono text-[10px] text-fg-2 tabular">{fmtPct(v)}%</span>}
                  </div>
                )
              })}
              {hover === n.key && (
                <div className="pointer-events-none absolute -top-2 right-0 z-10 translate-y-[-100%] rounded-md border border-line bg-surface-2 px-2 py-1.5 text-[10.5px] shadow-panel">
                  {groups.map((g, i) => <div key={g} className="flex items-center gap-1.5 tabular"><span className="size-2 rounded-sm" style={{ background: series(i) }} /><span className="text-fg-3">{g}</span><span className="ml-auto pl-3 font-mono text-fg">{n.cells[g]?.n ?? 0}/{n.cells[g]?.N ?? root.cells[g]?.N} ({fmtPct(pc(n, g))}%)</span></div>)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Generic({ table }: { table: Table }) {
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null)
  const [q, setQ] = useState('')
  const rows = useMemo(() => {
    let r = table.rows.filter(x => !q || Object.values(x).some(v => String(v ?? '').toLowerCase().includes(q.toLowerCase())))
    if (sort) r = [...r].sort((a, b) => {
      const x = a[sort.col], y = b[sort.col]
      const nx = Number(x), ny = Number(y)
      return (Number.isFinite(nx) && Number.isFinite(ny) ? nx - ny : String(x ?? '').localeCompare(String(y ?? ''))) * sort.dir
    })
    return r
  }, [table, sort, q])
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="text-[12px] font-semibold">{table.title || table.id}</div>
        <Badge tone="outline">not independently verified</Badge>
        <div className="relative ml-auto w-44"><Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-fg-3" /><Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter rows" className="h-7 pl-6 text-xs" /></div>
      </div>
      <div className="max-h-[60vh] overflow-auto rounded-xl border border-line">
        <table className="w-full border-separate border-spacing-0 text-[11.5px]">
          <thead className="sticky top-0 bg-surface-2"><tr>{table.columns.map(c => (
            <th key={c} onClick={() => setSort(s => (s?.col === c ? { col: c, dir: (s.dir * -1) as 1 | -1 } : { col: c, dir: 1 }))}
              className="cursor-pointer select-none border-b border-line px-2.5 py-1.5 text-left font-mono font-medium text-fg-2 hover:text-fg">{c}{sort?.col === c ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}</th>
          ))}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i} className="hover:bg-surface-3/40">{table.columns.map(c => <td key={c} className="border-b border-line-soft px-2.5 py-1 font-mono tabular">{r[c] == null ? <span className="text-fg-3">NA</span> : typeof r[c] === 'number' ? +(r[c] as number).toFixed(4) : String(r[c])}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <div className="text-[10.5px] text-fg-3">{rows.length} of {table.rows.length} rows</div>
    </div>
  )
}

/* ---------------- verification ---------------- */

const catLabel = (c: { level: string; soc: string | null; pt: string | null }) => (c.level === 'any' ? 'Any TEAE' : c.level === 'soc' ? c.soc : `${c.soc} › ${c.pt}`)

function Verification({ wid, ws, onTrace, send }: { wid: string; ws: WorkspaceState; onTrace: (c: CellRef) => void; send: Send }) {
  const run = ws.snap?.run
  const val = ws.snap?.validation
  const [filter, setFilter] = useState<'mismatch' | 'passed' | 'all'>('mismatch')
  if (!run) return <Empty icon={<ShieldCheck />} title="Nothing to verify yet" />
  if (!val) return <Empty icon={<ShieldCheck />} title="Not verified">{run.status !== 'succeeded' ? 'The last run did not succeed.' : 'Verification needs a table of type teae_summary and an approved specification.'}</Empty>
  return (
    <div className="space-y-3 p-3">
      {val.tables.map(t => {
        const cells = t.cells.filter(c => filter === 'all' || c.status === filter)
        return (
          <div key={t.table_id} className="space-y-3">
            <div className="grid grid-cols-3 gap-2">
              <Big icon={<CircleCheck />} tone="ok" n={t.passed} label="Passed" />
              <Big icon={<CircleX />} tone="bad" n={t.mismatch} label="Mismatch" />
              <Big icon={<CircleDashed />} tone="neutral" n={t.not_verified} label="Not verified" />
            </div>
            <div className="text-[11px] leading-relaxed text-fg-3">
              Table <span className="font-mono text-fg-2">{t.table_id}</span> from program v{val.program_version}, compared against a reference recomputed in Python from
              specification v{val.spec_version} (independent of the R code). {t.reason}
            </div>
            {(t.pattern?.length ?? 0) > 0 && (
              <ul className="space-y-0.5 rounded-lg border border-bad/25 bg-bad-soft/40 px-3 py-2 text-[11px] text-fg-2">
                {t.pattern!.map(f => <li key={f} className="flex gap-1.5"><span className="text-bad">•</span>{f}</li>)}
              </ul>
            )}
            {t.status !== 'not_verified' && (
              <>
                <div className="flex items-center gap-1.5">
                  <Seg value={filter} on={setFilter} opts={[['mismatch', `Mismatches ${t.mismatch}`], ['passed', `Passed ${t.passed}`], ['all', 'All']]} />
                  {t.mismatch > 0 && <Button size="sm" variant="primary" className="ml-auto" disabled={ws.snap?.agent_busy} onClick={() => send(`Validation found ${t.mismatch} mismatched cells in table ${t.table_id}. Investigate the cause using the validation results, lineage and the program, explain it, and propose a corrected program. Do not change the specification.`)}><Bot /> Ask the agent to investigate</Button>}
                </div>
                <div className="overflow-hidden rounded-xl border border-line">
                  {cells.length === 0 && <div className="px-3 py-4 text-center text-[11.5px] text-fg-3">{filter === 'mismatch' ? 'No mismatches: every reported cell agrees with the reference.' : 'No cells.'}</div>}
                  {cells.slice(0, 300).map((c, i) => (
                    <div key={i} className="flex items-center gap-2 border-b border-line-soft px-3 py-1.5 text-[11.5px] last:border-0">
                      <StatusDot s={c.status} />
                      <div className="min-w-0 flex-1"><div className="truncate text-fg-2" title={catLabel(c) ?? ''}>{catLabel(c)}</div><div className="text-[10.5px] text-fg-3">{c.group}{c.reason ? ` · ${c.reason}` : ''}</div></div>
                      <div className="text-right font-mono text-[11px] tabular"><div className={c.status === 'mismatch' ? 'text-bad' : ''}>{c.n ?? '–'}/{c.N ?? '–'}</div><div className="text-fg-3">ref {c.ref_n ?? '–'}/{c.ref_N ?? '–'}</div></div>
                      <Tip content="Trace lineage"><Button size="iconsm" variant="ghost" onClick={() => onTrace({ table_id: t.table_id, level: c.level, soc: c.soc, pt: c.pt, group: c.group, n: c.n, N: c.N })}><Target /></Button></Tip>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )
      })}
      <div className="rounded-lg border border-line-soft bg-surface-2/60 px-3 py-2 text-[11px] leading-relaxed text-fg-3">
        Agreement shows the program computes what the approved specification defines. It does not show the specification is the right
        analysis, that the data are correct, or that the output is fit for regulatory use. That needs your review.
      </div>
      <SignOff wid={wid} ws={ws} />
    </div>
  )
}

const Big = ({ icon, tone, n, label }: { icon: React.ReactNode; tone: 'ok' | 'bad' | 'neutral'; n: number; label: string }) => (
  <div className={cn('rounded-xl border px-3 py-2.5', tone === 'ok' ? 'border-ok/25 bg-ok-soft/50' : tone === 'bad' ? (n ? 'border-bad/30 bg-bad-soft/60' : 'border-line bg-surface-2') : 'border-line bg-surface-2')}>
    <div className={cn('flex items-center gap-1.5 text-[11px] [&_svg]:size-3.5', tone === 'ok' ? 'text-ok' : tone === 'bad' && n ? 'text-bad' : 'text-fg-3')}>{icon}{label}</div>
    <div className="mt-0.5 text-[22px] font-semibold tabular tracking-tight">{n}</div>
  </div>
)

function SignOff({ wid, ws }: { wid: string; ws: WorkspaceState }) {
  const run = ws.snap!.run!
  const val = ws.snap?.validation
  const [name, setName] = useState(() => { try { return localStorage.getItem('tp-reviewer') ?? '' } catch { return '' } })
  const [note, setNote] = useState('')
  const [ack, setAck] = useState(false)
  const mismatches = val?.tables.reduce((a, t) => a + t.mismatch, 0) ?? 0
  const verified = val?.tables.some(t => t.status !== 'not_verified')
  const canApprove = run.status === 'succeeded' && verified && mismatches === 0 && ack && name.trim()
  const mine = (ws.snap?.approvals ?? []).filter(a => a.run_id === run.id)
  const submit = async (decision: 'approved' | 'changes_requested') => {
    try {
      try { localStorage.setItem('tp-reviewer', name) } catch { /* storage unavailable */ }
      await api.signoff(wid, { run_id: run.id, reviewer: name, decision, note })
      toast.success(decision === 'approved' ? 'Signed off' : 'Changes requested')
      setNote(''); setAck(false)
    } catch (e) { toast.error(String((e as Error).message)) }
  }
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold"><ShieldCheck className="size-3.5 text-accent" /> Human review</div>
      {mine.map(a => (
        <div key={a.id} className={cn('mb-2 rounded-md px-2.5 py-1.5 text-[11px]', a.decision === 'approved' ? 'bg-ok-soft text-ok' : 'bg-warn-soft text-warn')}>
          {a.reviewer} {a.decision === 'approved' ? 'signed off' : 'requested changes'} · {ago(a.created_at)}{a.note ? ` · ${a.note}` : ''}
        </div>
      ))}
      <div className="grid gap-1.5">
        <Input value={name} onChange={e => setName(e.target.value)} placeholder="Reviewer name" className="h-7 text-xs" />
        <Input value={note} onChange={e => setNote(e.target.value)} placeholder="Review note" className="h-7 text-xs" />
        <label className="flex items-start gap-2 py-1 text-[11px] leading-snug text-fg-2">
          <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} className="mt-0.5 accent-[var(--accent)]" />
          I reviewed the specification, the program (v{run.program_version}) and the results myself; the automated comparison is not a substitute for that review.
        </label>
        <div className="flex gap-1.5">
          <Button size="sm" variant="outline" disabled={!name.trim()} onClick={() => submit('changes_requested')}>Request changes</Button>
          <Button size="sm" variant="ok" className="flex-1" disabled={!canApprove} onClick={() => submit('approved')}>Sign off program v{run.program_version}</Button>
        </div>
        {!canApprove && <div className="text-[10.5px] text-fg-3">{run.status !== 'succeeded' ? 'The run did not succeed.' : !verified ? 'No verified table in this run.' : mismatches ? `${mismatches} mismatches must be resolved first.` : !name.trim() ? 'Enter your name.' : !ack ? 'Confirm your review.' : ''}</div>}
      </div>
    </div>
  )
}

/* ---------------- lineage ---------------- */

function Lineage({ ws, send, onReveal }: { ws: WorkspaceState; send: Send; onReveal: (a: number, b: number, n: string) => void }) {
  const t = ws.trace
  const [all, setAll] = useState(false)
  if (!t) return <Empty icon={<Fingerprint />} title="Select a number to trace">Click any value in the results table and choose “Trace lineage” to see its source data, population, denominator, calculation and code.</Empty>
  const label = t.cell.level === 'any' ? 'Subjects with ≥1 TEAE' : t.cell.level === 'soc' ? t.cell.soc : t.cell.pt
  const r = t.reference
  const agree = r && t.program_value && Number(t.program_value.n) === r.n && Number(t.program_value.N) === r.N
  const steps: [string, React.ReactNode][] = r ? [
    ['Source', <span className="font-mono">{t.source!.subject} (subjects) · {t.source!.events} (events)</span>],
    ['Population', <span className="font-mono">{t.population!.join(' & ')}</span>],
    ['Group', <span className="font-mono">{t.group}</span>],
    ['Denominator', <><span className="font-mono font-semibold">N = {r.N}</span><div className="text-fg-3">{t.denominator}</div></>],
    ['Events counted', <span className="font-mono">{t.event_filter!.join(' & ') || 'all records'}</span>],
    ['Numerator', <><span className="font-mono font-semibold">n = {r.n}</span> <span className="text-fg-3">from {r.record_count} event record{r.record_count === 1 ? '' : 's'}</span><div className="text-fg-3">{t.numerator}</div></>],
    ['Calculation', <span className="font-mono">{r.n} / {r.N} × 100 = {fmtPct(pct(r.n, r.N))}%</span>],
  ] : []
  return (
    <div className="space-y-3 p-3">
      <div>
        <div className="text-[11px] text-fg-3">{t.cell.group} · table {t.table_id} · program v{t.program_version}{t.spec_version ? ` · spec v${t.spec_version}` : ''}</div>
        <div className="text-[14px] font-semibold leading-snug">{label}</div>
      </div>
      {!r ? <div className="text-[12px] text-fg-3">{t.note}</div> : (
        <>
          <div className={cn('flex items-center gap-2 rounded-lg px-3 py-2 text-[11.5px]', agree ? 'bg-ok-soft text-ok' : 'bg-bad-soft text-bad')}>
            {agree ? <CircleCheck className="size-3.5" /> : <CircleX className="size-3.5" />}
            Program reported {String(t.program_value?.n ?? '–')}/{String(t.program_value?.N ?? '–')}; independent reference {r.n}/{r.N}
          </div>
          <ol className="relative ml-2 border-l border-line">
            {steps.map(([k, v], i) => (
              <motion.li key={k} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }} className="relative pb-3 pl-4 last:pb-0">
                <span className="absolute -left-[5px] top-1 size-2.5 rounded-full border-2 border-surface bg-accent" />
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-fg-3">{k}</div>
                <div className="mt-0.5 text-[11.5px] leading-relaxed text-fg">{v}</div>
              </motion.li>
            ))}
          </ol>
          {r.subjects.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-semibold text-fg-2">Contributing subjects ({r.subjects.length})</div>
              <div className="flex flex-wrap gap-1">
                {(all ? r.subjects : r.subjects.slice(0, 24)).map(s => <span key={s} className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-fg-2">{s}</span>)}
                {r.subjects.length > 24 && <button onClick={() => setAll(a => !a)} className="text-[10.5px] text-accent">{all ? 'fewer' : `+${r.subjects.length - 24} more`}</button>}
              </div>
            </div>
          )}
          {(t.records_sample?.length ?? 0) > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-semibold text-fg-2">Source records (first {t.records_sample!.length} of {r.record_count})</div>
              <div className="max-h-52 overflow-auto rounded-lg border border-line">
                <table className="w-full border-separate border-spacing-0 font-mono text-[10.5px]">
                  <thead className="sticky top-0 bg-surface-2"><tr>{Object.keys(t.records_sample![0]).map(k => <th key={k} className="border-b border-line px-2 py-1 text-left font-medium text-fg-3">{k}</th>)}</tr></thead>
                  <tbody>{t.records_sample!.map((row, i) => <tr key={i}>{Object.values(row).map((v, j) => <td key={j} className="max-w-40 truncate border-b border-line-soft px-2 py-1">{v}</td>)}</tr>)}</tbody>
                </table>
              </div>
            </div>
          )}
          {(t.code_lines?.length ?? 0) > 0 && (
            <div>
              <div className="mb-1 text-[11px] font-semibold text-fg-2">Program lines using these variables</div>
              <div className="mb-1.5 text-[10.5px] text-fg-3">A text match on the specification's variables, not an execution trace. Ask the agent for the exact responsible lines.</div>
              <div className="overflow-hidden rounded-lg border border-line font-mono text-[10.5px]">
                {t.code_lines!.map(l => (
                  <button key={l.line} onClick={() => onReveal(l.line, l.line, `uses ${l.terms.join(', ')}`)} className="flex w-full gap-2 border-b border-line-soft px-2 py-1 text-left last:border-0 hover:bg-surface-3/60">
                    <span className="w-6 shrink-0 text-right text-fg-3">{l.line}</span><span className="truncate text-fg-2">{l.text.trim()}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <Button size="sm" className="w-full" disabled={ws.snap?.agent_busy} onClick={() => send('Explain how this number was derived, and highlight the R code responsible for it.', { selected_cell: { ...t.cell, table_id: t.table_id, value_n: t.program_value?.n, value_N: t.program_value?.N } })}>
            <Bot /> Ask the agent to explain this number
          </Button>
        </>
      )}
    </div>
  )
}
