import { AnimatePresence, motion } from 'motion/react'
import {
  ArrowUp, Bot, Braces, Check, CircleAlert, Code2, Database, FileCode2, GitCompare, KeyRound, ListTree, MessageCircleQuestion,
  Network, Play, Search, Settings2, ShieldCheck, Sparkles, Square, Table2, Target, User, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { toast } from 'sonner'
import { api, type Ev, type Health } from '@/lib/api'
import type { WorkspaceState } from '@/lib/useWorkspace'
import { clock, cn, mod } from '@/lib/utils'
import { condText } from './SpecView'
import type { CellRef } from './Workspace'
import { Badge, Button, Kbd, Spinner } from './ui'

const TOOL: Record<string, { icon: typeof Bot; label: (a: Record<string, any>) => string }> = {
  list_datasets: { icon: Database, label: () => 'Listing datasets' },
  inspect_dataset: { icon: Search, label: a => `Inspecting ${a.name}${a.columns?.length ? ` · ${a.columns.slice(0, 4).join(', ')}${a.columns.length > 4 ? '…' : ''}` : ''}` },
  tabulate: { icon: Table2, label: a => `Tabulating ${a.dataset}.${a.column}${a.by ? ` by ${a.by}` : ''}` },
  relationships: { icon: Network, label: () => 'Checking dataset relationships' },
  get_spec: { icon: ListTree, label: () => 'Reading the analysis specification' },
  propose_spec: { icon: ListTree, label: () => 'Proposing an analysis specification' },
  ask_user: { icon: MessageCircleQuestion, label: () => 'Asking you a question' },
  read_program: { icon: FileCode2, label: () => 'Reading program.R' },
  write_program: { icon: Code2, label: a => `Writing program.R${a.summary ? ` · ${a.summary}` : ''}` },
  run_program: { icon: Play, label: () => 'Executing R in the sandbox' },
  get_validation: { icon: ShieldCheck, label: () => 'Reading validation results' },
  trace_cell: { icon: Target, label: a => `Tracing ${a.level === 'any' ? 'any TEAE' : a.level === 'soc' ? a.soc : a.pt} · ${a.group}` },
  highlight_code: { icon: Braces, label: a => `Highlighting lines ${a.start_line}–${a.end_line}` },
}

type Item =
  | { t: 'user'; ev: Ev } | { t: 'agent_text'; ev: Ev } | { t: 'start'; ev: Ev } | { t: 'end'; ev: Ev }
  | { t: 'tool'; call: Ev; result?: Ev } | { t: 'spec'; ev: Ev } | { t: 'question'; ev: Ev } | { t: 'error'; ev: Ev }
  | { t: 'note'; ev: Ev }

function build(events: Ev[]): Item[] {
  const out: Item[] = []
  const tools = new Map<string, Item & { t: 'tool' }>()
  for (const ev of events) {
    const p = ev.payload
    switch (ev.kind) {
      case 'user_message': out.push({ t: 'user', ev }); break
      case 'assistant_message': out.push({ t: 'agent_text', ev }); break
      case 'agent_start': out.push({ t: 'start', ev }); break
      case 'agent_end': out.push({ t: 'end', ev }); break
      case 'tool_call': { const it = { t: 'tool' as const, call: ev }; tools.set(p.call_id, it); out.push(it); break }
      case 'tool_result': { const it = tools.get(p.call_id); if (it) it.result = ev; break }
      case 'spec_proposed': if (p.by === 'agent') out.push({ t: 'spec', ev }); break
      case 'clarification': out.push({ t: 'question', ev }); break
      case 'agent_error': out.push({ t: 'error', ev }); break
      case 'agent_cancelled': out.push({ t: 'note', ev }); break
      case 'run_end': if (p.by === 'user') out.push({ t: 'note', ev }); break
      case 'program_version': if (p.author === 'user') out.push({ t: 'note', ev }); break
      case 'spec_decided': out.push({ t: 'note', ev }); break
      case 'signoff': out.push({ t: 'note', ev }); break
      case 'validation': out.push({ t: 'note', ev }); break
    }
  }
  return out
}

export default function AgentPanel({ wid, ws, send, cancel, cell, clearCell, composerRef, health, onSettings, onAck, onReveal, openSpec, openDiff }: {
  wid: string; ws: WorkspaceState; send: (t: string, c?: Record<string, unknown>) => void; cancel: () => void
  cell: CellRef | null; clearCell: () => void; composerRef: RefObject<HTMLTextAreaElement | null>; health: Health | null
  onSettings: () => void; onAck: () => void; onReveal: (a: number, b: number, n: string) => void; openSpec: () => void; openDiff: () => void
}) {
  const items = useMemo(() => build(ws.events), [ws.events])
  const busy = !!ws.snap?.agent_busy
  const scroller = useRef<HTMLDivElement>(null)
  const [text, setText] = useState('')
  const stick = useRef(true)

  useEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [items.length, ws.draft, ws.activeTool, ws.codeStream?.text.length])

  const lastUser = [...ws.events].reverse().find(e => e.kind === 'user_message')
  const submit = (t = text) => {
    const v = t.trim()
    if (!v || busy) return
    send(v, cell ? { selected_cell: cell } : undefined)
    setText('')
    clearCell()
    stick.current = true
  }

  const sugg = suggestions(ws)
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line-soft px-3">
        <Sparkles className="size-3.5 text-violet" />
        <span className="text-xs font-semibold">Agent</span>
        {busy ? <Badge tone="violet"><Spinner className="size-3" /> working</Badge> : <span className="text-[11px] text-fg-3">{health?.llm.configured ? `${health.llm.model} via ${health.llm.host}` : 'not connected'}</span>}
        <span className="ml-auto text-[10.5px] text-fg-3"><Kbd>{mod}</Kbd><Kbd>K</Kbd> focus</span>
      </div>

      <div ref={scroller} onScroll={e => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40 }}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {items.length === 0 && !busy && (
          <div className="flex h-full flex-col items-center justify-center gap-1.5 text-center">
            <div className="grid size-9 place-items-center rounded-xl bg-violet-soft text-violet"><Bot className="size-4" /></div>
            <div className="text-[13px] font-medium text-fg-2">Direct the AI programmer</div>
            <div className="max-w-sm text-xs text-fg-3">It inspects the data with real tools, proposes a specification for your approval, writes R, runs it, and checks the result against an independent engine.</div>
          </div>
        )}
        <div className="space-y-1.5">
          {items.map((it, i) => <Row key={i} it={it} wid={wid} ws={ws} busy={busy} send={send} onSettings={onSettings} onAck={onAck} onReveal={onReveal} openSpec={openSpec} openDiff={openDiff} lastUser={lastUser} />)}
          {busy && ws.activeTool && !items.some(x => x.t === 'tool' && !x.result) && (
            <div className="flex items-center gap-2 py-1 pl-1 text-[11.5px] text-fg-3">
              <Spinner className="size-3 text-violet" />
              {ws.activeTool === 'write_program' && ws.codeStream ? `Writing program.R · ${ws.codeStream.text.split('\n').length} lines so far` : `Calling ${ws.activeTool}…`}
            </div>
          )}
          {ws.draft && (
            <div className="flex gap-2 pt-1">
              <Bot className="mt-0.5 size-3.5 shrink-0 text-violet" />
              <div className="tp-caret whitespace-pre-wrap text-[12.5px] leading-relaxed text-fg"><Md text={ws.draft} /></div>
            </div>
          )}
          {busy && !ws.draft && !ws.activeTool && !items.some(x => x.t === 'tool' && !x.result) && (
            <div className="flex items-center gap-2 py-1 pl-1 text-[11.5px] text-fg-3"><Spinner className="size-3 text-violet" /> Waiting for the model…</div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-line-soft p-2">
        {sugg.length > 0 && !busy && !text && (
          <div className="mb-1.5 flex flex-wrap gap-1">
            {sugg.map(s => <button key={s} onClick={() => submit(s)} className="rounded-full border border-line bg-surface-2 px-2.5 py-1 text-[11px] text-fg-2 transition hover:border-accent/50 hover:text-fg">{s}</button>)}
          </div>
        )}
        <div className="rounded-xl border border-line bg-bg transition focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15">
          {cell && (
            <div className="flex items-center gap-1.5 px-2.5 pt-2">
              <Badge tone="accent"><Target /> {cell.level === 'any' ? 'Any TEAE' : cell.level === 'soc' ? cell.soc : cell.pt} · {cell.group} · n={String(cell.n)}</Badge>
              <button onClick={clearCell} className="text-fg-3 hover:text-fg"><X className="size-3" /></button>
            </div>
          )}
          <textarea
            ref={composerRef} value={text} rows={1} disabled={busy}
            onChange={e => { setText(e.target.value); e.currentTarget.style.height = 'auto'; e.currentTarget.style.height = Math.min(160, e.currentTarget.scrollHeight) + 'px' }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
            placeholder={busy ? 'The agent is working…' : cell ? 'Ask about this number…' : 'Tell the agent what to analyze, change or explain'}
            className="block max-h-40 w-full resize-none bg-transparent px-3 py-2.5 text-[13px] text-fg placeholder:text-fg-3 outline-none disabled:opacity-60"
          />
          <div className="flex items-center justify-between px-2 pb-1.5">
            <span className="text-[10.5px] text-fg-3">Enter to send · Shift+Enter for a new line</span>
            {busy
              ? <Button size="xs" variant="danger" onClick={cancel}><Square /> Stop</Button>
              : <Button size="iconsm" variant="primary" onClick={() => submit()} disabled={!text.trim()} aria-label="Send"><ArrowUp /></Button>}
          </div>
        </div>
      </div>
    </div>
  )
}

function suggestions(ws: WorkspaceState): string[] {
  const s = ws.snap
  if (!s || !s.datasets.length) return []
  const approved = s.specs.some(x => x.status === 'approved')
  const v = s.validation?.tables.some(t => t.status === 'mismatch')
  if (s.specs.some(x => x.status === 'proposed')) return []
  if (v) return ['Investigate the validation mismatches and propose a fix']
  if (!approved) return ['Create a treatment-emergent adverse event summary by treatment group', 'Which analysis populations are defined in ADSL?', 'How does ADAE link to ADSL?']
  if (s.run?.status === 'succeeded') return ['Use the efficacy population instead', 'Exclude participants without treatment exposure', 'Explain why the treatment groups differ', 'Add a Total column']
  return []
}

function Row({ it, wid, ws, busy, send, onSettings, onAck, onReveal, openSpec, openDiff, lastUser }: {
  it: Item; wid: string; ws: WorkspaceState; busy: boolean; send: (t: string, c?: Record<string, unknown>) => void; onSettings: () => void; onAck: () => void
  onReveal: (a: number, b: number, n: string) => void; openSpec: () => void; openDiff: () => void; lastUser?: Ev
}) {
  switch (it.t) {
    case 'user':
      return (
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="flex justify-end pt-2">
          <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent-soft px-3 py-2 text-[12.5px] leading-relaxed text-fg">
            {it.ev.payload.context?.selected_cell && (
              <div className="mb-1 flex items-center gap-1 text-[10.5px] text-accent"><Target className="size-3" />
                {(() => { const c = it.ev.payload.context.selected_cell; return `${c.level === 'any' ? 'Any TEAE' : c.level === 'soc' ? c.soc : c.pt} · ${c.group}` })()}</div>
            )}
            <span className="whitespace-pre-wrap">{it.ev.payload.text}</span>
          </div>
          <User className="ml-1.5 mt-1.5 size-3.5 shrink-0 text-fg-3" />
        </motion.div>
      )
    case 'start':
      return <div className="flex items-center gap-2 pt-1 text-[10px] uppercase tracking-wider text-fg-3"><span className="h-px flex-1 bg-line-soft" />{it.ev.payload.model} · {clock(it.ev.ts)}<span className="h-px flex-1 bg-line-soft" /></div>
    case 'end':
      return <div className="pl-6 text-[10px] text-fg-3">done in {it.ev.payload.seconds}s{it.ev.payload.usage?.completion_tokens ? ` · ${it.ev.payload.usage.prompt_tokens.toLocaleString()} in / ${it.ev.payload.usage.completion_tokens.toLocaleString()} out tokens` : ''}</div>
    case 'agent_text':
      return <div className="flex gap-2 pt-1"><Bot className="mt-0.5 size-3.5 shrink-0 text-violet" /><div className="min-w-0 text-[12.5px] leading-relaxed text-fg"><Md text={it.ev.payload.text} /></div></div>
    case 'tool': {
      const p = it.call.payload
      const T = TOOL[p.name] ?? { icon: Braces, label: () => p.name }
      const r = it.result?.payload
      const failed = r && r.ok === false
      return (
        <motion.div initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} className={cn('group ml-5 flex items-center gap-2 rounded-lg border px-2.5 py-1.5', failed ? 'border-bad/30 bg-bad-soft/50' : 'border-line-soft bg-surface-2/60')}>
          <T.icon className={cn('size-3.5 shrink-0', failed ? 'text-bad' : 'text-fg-3')} />
          <span className="min-w-0 flex-1 truncate text-[11.5px] text-fg-2">{T.label(p.args ?? {})}</span>
          {r ? <span className={cn('max-w-[45%] truncate text-[11px]', failed ? 'text-bad' : 'text-fg-3')}>{r.summary}</span> : <Spinner className="size-3 text-violet" />}
          {r && !failed && p.name === 'write_program' && <button onClick={openDiff} className="text-[11px] text-accent opacity-0 transition group-hover:opacity-100"><GitCompare className="inline size-3" /> diff</button>}
          {p.name === 'highlight_code' && <button onClick={() => onReveal(p.args.start_line, p.args.end_line, p.args.note)} className="text-[11px] text-accent">show</button>}
          {r && (failed ? <CircleAlert className="size-3 text-bad" /> : <Check className="size-3 text-ok" />)}
        </motion.div>
      )
    }
    case 'spec': {
      const p = it.ev.payload
      const current = ws.snap?.specs.find(s => s.version === p.version)
      const open = current?.status === 'proposed'
      return (
        <div className="ml-5 rounded-xl border border-violet/40 bg-violet-soft/40 p-3">
          <div className="mb-1.5 flex items-center gap-2 text-[12px] font-semibold"><ListTree className="size-3.5 text-violet" /> Specification v{p.version}
            <Badge tone={open ? 'violet' : current?.status === 'approved' ? 'ok' : 'neutral'}>{open ? 'awaiting your decision' : current?.status}</Badge></div>
          <div className="space-y-0.5 text-[11.5px] text-fg-2">
            <div><span className="text-fg-3">Population </span>{(p.spec.population ?? []).map(condText).join(' & ') || 'all subjects'}</div>
            <div><span className="text-fg-3">Groups </span><span className="font-mono">{p.spec.treatment_var}</span> · <span className="text-fg-3">Events </span>{(p.spec.event_filter ?? []).map(condText).join(' & ') || 'all'}</div>
          </div>
          {p.rationale && <div className="mt-1.5 text-[11.5px] leading-relaxed text-fg-3">{p.rationale}</div>}
          {open && (
            <div className="mt-2 flex gap-1.5">
              <Button size="xs" variant="ok" disabled={busy} onClick={async () => { try { await api.decide(wid, p.version, 'approve'); send(`I approved specification v${p.version}. Go ahead.`) } catch (e) { toast.error(String((e as Error).message)) } }}><Check /> Approve</Button>
              <Button size="xs" variant="ghost" onClick={openSpec}>Review details</Button>
            </div>
          )}
        </div>
      )
    }
    case 'question': {
      const p = it.ev.payload
      return (
        <div className="ml-5 rounded-xl border border-accent/35 bg-accent-soft/50 p-3">
          <div className="flex gap-2 text-[12.5px] text-fg"><MessageCircleQuestion className="mt-0.5 size-3.5 shrink-0 text-accent" />{p.question}</div>
          {p.options?.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5 pl-5">{p.options.map((o: string) => <Button key={o} size="xs" disabled={busy} onClick={() => send(o)}>{o}</Button>)}</div>}
        </div>
      )
    }
    case 'error': {
      const p = it.ev.payload
      if (p.code === 'needs_ack') return <AckCard host={p.host} onAck={async () => { await api.ack(p.host); onAck(); if (lastUser) send(lastUser.payload.text, lastUser.payload.context) }} />
      return (
        <div className="ml-5 flex items-start gap-2 rounded-lg border border-bad/30 bg-bad-soft/50 px-3 py-2 text-[11.5px] text-fg-2">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-bad" />
          <div className="min-w-0 flex-1 break-words">{p.message}</div>
          {p.code === 'not_configured' && <Button size="xs" onClick={onSettings}><Settings2 /> Settings</Button>}
          {(p.code === 'model' || p.code === 'steps' || p.code === 'length') && lastUser && <Button size="xs" disabled={busy} onClick={() => send(p.code === 'steps' ? 'Continue.' : lastUser.payload.text, lastUser.payload.context)}>{p.code === 'steps' ? 'Continue' : 'Retry'}</Button>}
        </div>
      )
    }
    case 'note': {
      const { kind, payload: p } = it.ev
      let node: React.ReactNode = null
      if (kind === 'agent_cancelled') node = <><Square className="size-3" /> Stopped by you</>
      else if (kind === 'run_end') node = <><Play className="size-3" /> You ran program v{p.program_version}: <span className={p.status === 'succeeded' ? 'text-ok' : 'text-bad'}>{p.status}</span></>
      else if (kind === 'program_version') node = <><User className="size-3" /> You saved v{p.version}{p.previous ? ` (+${p.added} −${p.removed})` : ''}</>
      else if (kind === 'spec_decided') node = <><ListTree className="size-3" /> Spec v{p.version} {p.status}{p.note ? `: ${p.note}` : ''}</>
      else if (kind === 'signoff') node = <><ShieldCheck className="size-3" /> {p.reviewer} {p.decision === 'approved' ? 'signed off' : 'requested changes on'} program v{p.program_version}</>
      else if (kind === 'validation') {
        const bad = (p.tables ?? []).reduce((a: number, t: { mismatch: number }) => a + t.mismatch, 0)
        const ok = (p.tables ?? []).reduce((a: number, t: { passed: number }) => a + t.passed, 0)
        const nv = (p.tables ?? []).every((t: { status: string }) => t.status === 'not_verified')
        node = <><ShieldCheck className={cn('size-3', nv ? '' : bad ? 'text-bad' : 'text-ok')} /> Validation of v{p.program_version}: {nv ? 'not verified' : <><span className="text-ok">{ok} passed</span>{bad > 0 && <>, <span className="text-bad">{bad} mismatch</span></>}</>}</>
      }
      return <div className="flex items-center gap-1.5 pl-5 text-[11px] text-fg-3">{node}</div>
    }
  }
}

function AckCard({ host, onAck }: { host: string; onAck: () => void }) {
  return (
    <AnimatePresence>
      <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="ml-5 rounded-xl border border-warn/40 bg-warn-soft/50 p-3">
        <div className="flex items-center gap-2 text-[12px] font-semibold text-fg"><KeyRound className="size-3.5 text-warn" /> Authorize sending data to {host}?</div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-fg-2">
          The agent sends the model: dataset and column names, labels, value counts and frequency tables it requests, the R program,
          console output, and the first rows of result tables. Full datasets are not sent. Only authorize an endpoint you are permitted
          to share this data with (for clinical data: an approved, access-controlled deployment).
        </p>
        <div className="mt-2 flex justify-end"><Button size="xs" variant="primary" onClick={onAck}>I'm authorized · continue</Button></div>
      </motion.div>
    </AnimatePresence>
  )
}

/** Minimal markdown: **bold**, `code`, bullet lines. */
function Md({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <>
      {lines.map((ln, i) => {
        const bullet = /^\s*[-*] /.test(ln)
        const body = bullet ? ln.replace(/^\s*[-*] /, '') : ln.replace(/^#+ /, '')
        const parts = body.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((s, j) =>
          s.startsWith('**') ? <strong key={j} className="font-semibold">{s.slice(2, -2)}</strong>
            : s.startsWith('`') ? <code key={j} className="rounded bg-surface-3 px-1 font-mono text-[11.5px]">{s.slice(1, -1)}</code> : s)
        return bullet ? <div key={i} className="flex gap-1.5 pl-1"><span className="text-fg-3">•</span><span>{parts}</span></div>
          : <div key={i} className={cn(!ln.trim() && 'h-2', /^#+ /.test(ln) && 'font-semibold')}>{parts}</div>
      })}
    </>
  )
}
