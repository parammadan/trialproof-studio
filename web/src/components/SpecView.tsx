import Editor from '@monaco-editor/react'
import { Bot, Check, ListTree, Pencil, User, X } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { api, type Cond, type Spec, type SpecRow } from '@/lib/api'
import type { WorkspaceState } from '@/lib/useWorkspace'
import { ago, cn } from '@/lib/utils'
import { Badge, Button, Empty, Input } from './ui'

export const condText = (c: Cond) => {
  const sym: Record<string, string> = { eq: '=', ne: '≠', gt: '>', ge: '≥', lt: '<', le: '≤' }
  if (sym[c.op]) return `${c.var} ${sym[c.op]} ${JSON.stringify(c.value)}`
  if (c.op === 'in' || c.op === 'not_in') return `${c.var} ${c.op === 'in' ? '∈' : '∉'} {${(c.value as unknown[]).join(', ')}}`
  return `${c.var} ${c.op === 'missing' ? 'is missing' : 'is not missing'}`
}

const fields = (s: Spec): [string, string, React.ReactNode][] => [
  ['type', 'Analysis', s.type === 'teae_summary' ? 'TEAE incidence summary' : s.type],
  ['datasets', 'Data', <span className="font-mono">{s.datasets.subject} → {s.datasets.events}</span>],
  ['population', 'Population', (s.population ?? []).length ? <Chips c={s.population!} /> : <span className="text-fg-3">all subjects</span>],
  ['treatment_var', 'Treatment group', <span className="font-mono">{s.datasets.subject}.{s.treatment_var}</span>],
  ['event_filter', 'Events counted', (s.event_filter ?? []).length ? <Chips c={s.event_filter!} /> : <span className="text-fg-3">all records</span>],
  ['coding', 'Coding', <span className="font-mono">{s.soc_var} / {s.pt_var}</span>],
  ['denominator', 'Denominator', <span>distinct {s.subject_id ?? 'USUBJID'} in the population, per group</span>],
  ['include_total', 'Total column', s.include_total ? 'yes' : 'no'],
]
const val = (s: Spec, k: string) => JSON.stringify(k === 'coding' ? [s.soc_var, s.pt_var] : k === 'denominator' ? s.subject_id : (s as Record<string, unknown>)[k])
const Chips = ({ c }: { c: Cond[] }) => <div className="flex flex-wrap gap-1">{c.map((x, i) => <span key={i} className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[11px]">{condText(x)}</span>)}</div>

function SpecCard({ row, against, tone }: { row: SpecRow; against?: Spec; tone: 'approved' | 'proposed' }) {
  return (
    <div className={cn('overflow-hidden rounded-xl border', tone === 'proposed' ? 'border-violet/40 bg-violet-soft/40' : 'border-line bg-surface-2')}>
      <div className="flex items-center gap-2 border-b border-line-soft px-3.5 py-2">
        <span className="text-[12px] font-semibold">{row.spec.title || 'Analysis specification'}</span>
        <Badge tone={tone === 'proposed' ? 'violet' : 'ok'}>v{row.version} · {tone === 'proposed' ? 'awaiting your decision' : 'approved'}</Badge>
        <span className="ml-auto flex items-center gap-1 text-[10.5px] text-fg-3">{row.proposed_by === 'agent' ? <Bot className="size-3" /> : <User className="size-3" />} {row.proposed_by} · {ago(row.created_at)}</span>
      </div>
      <dl className="divide-y divide-line-soft">
        {fields(row.spec).map(([k, label, node]) => {
          const changed = against && val(against, k) !== val(row.spec, k)
          return (
            <div key={k} className={cn('grid grid-cols-[120px_1fr] gap-3 px-3.5 py-2 text-[12px]', changed && 'bg-violet-soft')}>
              <dt className="text-fg-3">{label}{changed && <span className="ml-1.5 text-[10px] font-semibold text-violet">changed</span>}</dt>
              <dd className="min-w-0 text-fg">{node}</dd>
            </div>
          )
        })}
      </dl>
      {row.rationale && <div className="border-t border-line-soft px-3.5 py-2.5 text-[11.5px] leading-relaxed text-fg-2"><span className="font-medium text-fg-3">Rationale · </span>{row.rationale}</div>}
    </div>
  )
}

export default function SpecView({ wid, ws, send }: { wid: string; ws: WorkspaceState; send: (t: string) => void }) {
  const specs = ws.snap?.specs ?? []
  const approved = [...specs].reverse().find(s => s.status === 'approved')
  const pending = specs.find(s => s.status === 'proposed')
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  const decide = async (d: 'approve' | 'reject') => {
    if (!pending) return
    setBusy(true)
    try {
      await api.decide(wid, pending.version, d, note)
      if (pending.proposed_by === 'agent' && !ws.snap?.agent_busy)
        send(d === 'approve' ? `I approved specification v${pending.version}. Go ahead.` : `I rejected specification v${pending.version}.${note ? ' ' + note : ''}`)
      setNote('')
    } catch (e) { toast.error(String((e as Error).message)) } finally { setBusy(false) }
  }
  const saveEdit = async () => {
    try {
      const spec = JSON.parse(draft)
      await api.userSpec(wid, spec, 'edited by the user')
      setEditing(false)
      toast.success('Specification saved and approved')
    } catch (e) { toast.error(String((e as Error).message)) }
  }

  if (editing) return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line-soft px-3 py-1.5 text-[11px] text-fg-3">
        Editing the specification as JSON. Saving creates a new approved version, authored by you.
        <div className="ml-auto flex gap-1.5"><Button size="xs" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button><Button size="xs" variant="primary" onClick={saveEdit}>Save & approve</Button></div>
      </div>
      <div className="min-h-[300px] flex-1"><Editor language="json" theme="tp" value={draft} onChange={v => setDraft(v ?? '')} options={{ minimap: { enabled: false }, fontSize: 12, fontFamily: '"JetBrains Mono Variable", monospace', scrollBeyondLastLine: false, automaticLayout: true }} /></div>
    </div>
  )

  if (!approved && !pending) return (
    <Empty icon={<ListTree />} title="No analysis specification">
      The agent proposes one after inspecting the data; nothing is verified until you approve it. Ask for an analysis below, or
      <button className="ml-1 text-accent hover:underline" onClick={() => { setDraft(JSON.stringify(TEMPLATE, null, 2)); setEditing(true) }}>write one yourself</button>.
    </Empty>
  )

  return (
    <div className="space-y-3 p-3">
      {pending && (
        <div className="space-y-2">
          <SpecCard row={pending} against={approved?.spec} tone="proposed" />
          <div className="flex items-center gap-2">
            <Input value={note} onChange={e => setNote(e.target.value)} placeholder="Note (optional, sent with a rejection)" className="h-7 text-xs" />
            <Button size="sm" variant="danger" onClick={() => decide('reject')} disabled={busy}><X /> Reject</Button>
            <Button size="sm" variant="ok" onClick={() => decide('approve')} disabled={busy}><Check /> Approve v{pending.version}</Button>
          </div>
          <p className="text-[11px] text-fg-3">Approving lets the agent program against this definition, and the validation engine computes its reference from it.</p>
        </div>
      )}
      {approved && <SpecCard row={approved} tone="approved" />}
      {approved && (
        <div className="flex justify-end">
          <Button size="xs" variant="ghost" onClick={() => { setDraft(JSON.stringify(approved.spec, null, 2)); setEditing(true) }}><Pencil /> Edit as JSON</Button>
        </div>
      )}
      {specs.filter(s => s.status === 'rejected' || s.status === 'superseded' || (s.status === 'approved' && s !== approved)).length > 0 && (
        <div className="pt-1 text-[11px] text-fg-3">
          Earlier versions: {specs.filter(s => s !== approved && s !== pending).map(s => `v${s.version} ${s.status}`).join(' · ')}
        </div>
      )}
    </div>
  )
}

const TEMPLATE: Spec = {
  type: 'teae_summary', title: 'TEAEs by SOC and PT', datasets: { subject: 'ADSL', events: 'ADAE' }, subject_id: 'USUBJID',
  population: [{ var: 'SAFFL', op: 'eq', value: 'Y' }], treatment_var: 'TRT01A',
  event_filter: [{ var: 'TRTEMFL', op: 'eq', value: 'Y' }], soc_var: 'AEBODSYS', pt_var: 'AEDECOD', include_total: false,
}
