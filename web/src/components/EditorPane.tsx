import Editor, { DiffEditor, type OnMount } from '@monaco-editor/react'
import { AnimatePresence, motion } from 'motion/react'
import { Bot, FileCode2, GitCompare, History, ListTree, Terminal, User } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Theme } from '@/App'
import { api, type Program } from '@/lib/api'
import { monaco } from '@/lib/monaco'
import type { WorkspaceState } from '@/lib/useWorkspace'
import { ago, cn, mod } from '@/lib/utils'
import SpecView from './SpecView'
import type { CenterTab } from './Workspace'
import { Empty, Kbd, Spinner, Tabs, TabsContent, TabsList, TabsTrigger } from './ui'

type Ed = Parameters<OnMount>[0]

const OPTIONS: monaco.editor.IStandaloneEditorConstructionOptions = {
  fontFamily: '"JetBrains Mono Variable", ui-monospace, monospace', fontSize: 12.5, lineHeight: 20, fontLigatures: false,
  minimap: { enabled: false }, scrollBeyondLastLine: false, padding: { top: 10, bottom: 10 }, renderLineHighlight: 'all',
  smoothScrolling: true, cursorSmoothCaretAnimation: 'on', cursorBlinking: 'smooth', glyphMargin: false, folding: true,
  lineNumbersMinChars: 3, tabSize: 2, automaticLayout: true, scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
  formatOnPaste: false, wordWrap: 'off', stickyScroll: { enabled: false },
}

export default function EditorPane({ wid, ws, code, setCode, dirty, tab, setTab, theme, onRun, onSave, send }: {
  wid: string; ws: WorkspaceState; code: string; setCode: (c: string) => void; dirty: boolean; tab: CenterTab; setTab: (t: CenterTab) => void
  theme: Theme; onRun: () => void; onSave: () => void; send: (t: string, c?: Record<string, unknown>) => void
}) {
  const { snap, codeStream, run } = ws
  const program = snap?.program
  const edRef = useRef<Ed | null>(null)
  const decos = useRef<monaco.editor.IEditorDecorationsCollection | null>(null)
  const [cursor, setCursor] = useState({ line: 1, col: 1 })
  const streamVersion = useRef<number | null>(null)
  const pending = snap?.specs.find(s => s.status === 'proposed')

  // While the agent streams code, the editor shows the stream (read-only). The stream ends once the
  // saved version it produced arrives in the snapshot.
  const streaming = !!codeStream
  useEffect(() => {
    if (codeStream && streamVersion.current === null) streamVersion.current = program?.version ?? 0
  }, [codeStream, program?.version])
  useEffect(() => {
    if (codeStream && streamVersion.current !== null && (program?.version ?? 0) > streamVersion.current) {
      ws.setCodeStream(null)
      streamVersion.current = null
    }
    if (!codeStream) streamVersion.current = null
  }, [program?.version, codeStream, ws])
  useEffect(() => { if (streaming) setTab('editor') }, [streaming, setTab])

  const shown = streaming ? codeStream!.text : code

  // keep the stream's tail in view
  useEffect(() => {
    const ed = edRef.current
    if (!ed || !streaming) return
    const n = ed.getModel()?.getLineCount() ?? 1
    ed.revealLine(n, monaco.editor.ScrollType.Immediate)
  }, [shown, streaming])

  // decorations: executing / executed expressions, the last error, agent highlights
  const lastError = snap?.run && snap.run.status === 'failed' && snap.run.program_version === program?.version && !dirty
    ? (snap.run.result as { line?: number; end_line?: number; message?: string }) : null
  const hl = ws.highlight
  useEffect(() => {
    const ed = edRef.current
    if (!ed) return
    const d: monaco.editor.IModelDeltaDecoration[] = []
    const range = (a: number, b: number) => new monaco.Range(a, 1, b, 1)
    if (!streaming) {
      if (run.running || run.done.length) {
        run.done.forEach(([a, b]) => d.push({ range: range(a, b), options: { isWholeLine: true, linesDecorationsClassName: 'tp-gutter-done' } }))
        if (run.running && run.line) d.push({ range: range(run.line, run.endLine ?? run.line), options: { isWholeLine: true, className: 'tp-line-running', linesDecorationsClassName: 'tp-gutter-running' } })
      }
      if (lastError?.line && !run.running) {
        d.push({ range: range(lastError.line, lastError.end_line ?? lastError.line), options: { isWholeLine: true, className: 'tp-line-error', linesDecorationsClassName: 'tp-gutter-error', hoverMessage: { value: '**Error:** ' + (lastError.message ?? '') } } })
      }
      if (hl) d.push({ range: range(hl.start, hl.end), options: { isWholeLine: true, className: 'tp-line-highlight', linesDecorationsClassName: 'tp-gutter-highlight', hoverMessage: { value: hl.note } } })
    }
    if (!decos.current) decos.current = ed.createDecorationsCollection(d)
    else decos.current.set(d)
  }, [run, lastError?.line, lastError?.end_line, lastError?.message, hl, streaming])

  useEffect(() => {
    if (hl && edRef.current) edRef.current.revealLinesInCenter(hl.start, hl.end, monaco.editor.ScrollType.Smooth)
  }, [hl])
  useEffect(() => {
    if (run.running && run.line && edRef.current) edRef.current.revealLineInCenterIfOutsideViewport(run.line, monaco.editor.ScrollType.Smooth)
  }, [run.running, run.line])

  const onMount: OnMount = ed => {
    edRef.current = ed
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRunRef.current())
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => onSaveRef.current())
    ed.onDidChangeCursorPosition(e => setCursor({ line: e.position.lineNumber, col: e.position.column }))
  }
  const onRunRef = useRef(onRun); onRunRef.current = onRun
  const onSaveRef = useRef(onSave); onSaveRef.current = onSave

  const consoleCount = run.console.length
  return (
    <Tabs value={tab} onValueChange={v => setTab(v as CenterTab)} className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line-soft px-2">
        <TabsList>
          <TabsTrigger value="editor"><FileCode2 /> program.R {dirty && <span className="size-1.5 rounded-full bg-warn" />}</TabsTrigger>
          <TabsTrigger value="diff" disabled={(snap?.programs.length ?? 0) < 2}><GitCompare /> Changes</TabsTrigger>
          <TabsTrigger value="console"><Terminal /> Console {run.running ? <Spinner className="size-3 text-accent" /> : consoleCount > 0 && <span className="text-[10px] text-fg-3">{snap?.run?.status === 'failed' ? <span className="text-bad">error</span> : ''}</span>}</TabsTrigger>
          <TabsTrigger value="spec"><ListTree /> Spec {pending && <span className="size-1.5 animate-pulse rounded-full bg-violet" />}</TabsTrigger>
          <TabsTrigger value="history"><History /> History</TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="editor" forceMount className="relative min-h-0 flex-1">
        <AnimatePresence>
          {streaming && (
            <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
              className="absolute right-3 top-2 z-10 flex items-center gap-2 rounded-full border border-violet/40 bg-surface-2/90 px-3 py-1 text-[11px] text-violet shadow-panel backdrop-blur-xl">
              <Bot className="size-3.5" /> Agent is writing program.R
              <span className="tabular text-fg-3">{shown.split('\n').length} lines</span>
            </motion.div>
          )}
          {hl && !streaming && (
            <motion.div key={hl.ts} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className="absolute right-3 top-2 z-10 flex max-w-[70%] items-center gap-2 rounded-lg border border-warn/40 bg-surface-2/95 px-3 py-1.5 text-[11px] text-fg-2 shadow-panel backdrop-blur-xl">
              <span className="font-mono text-warn">L{hl.start}{hl.end !== hl.start ? `–${hl.end}` : ''}</span>
              <span className="truncate">{hl.note}</span>
              <button className="text-fg-3 hover:text-fg" onClick={() => ws.setHighlight(null)}>×</button>
            </motion.div>
          )}
        </AnimatePresence>
        {!program && !streaming && !code ? (
          <div className="absolute inset-0 z-[1] bg-[var(--bg)]">
            <Empty icon={<FileCode2 />} title="No program yet">
              Ask the agent below to build an analysis, or start typing R here. Read data with <code className="font-mono text-fg-2">tp_read("ADSL")</code>.
              <div className="mt-3"><button className="text-accent hover:underline" onClick={() => setCode('adsl <- tp_read("ADSL")\nglimpse(adsl)\n')}>Start an empty program</button></div>
            </Empty>
          </div>
        ) : null}
        <Editor
          language="r" theme="tp" value={shown} options={{ ...OPTIONS, readOnly: streaming }} onMount={onMount}
          onChange={v => { if (!streaming) setCode(v ?? '') }}
          key={theme}
        />
      </TabsContent>

      <TabsContent value="diff" className="min-h-0 flex-1"><DiffView wid={wid} ws={ws} theme={theme} /></TabsContent>
      <TabsContent value="console" className="min-h-0 flex-1"><ConsoleView ws={ws} onLine={(l: number) => ws.setHighlight({ start: l, end: l, note: 'from the console', ts: Date.now() })} /></TabsContent>
      <TabsContent value="spec" className="min-h-0 flex-1 overflow-y-auto"><SpecView wid={wid} ws={ws} send={send} /></TabsContent>
      <TabsContent value="history" className="min-h-0 flex-1 overflow-y-auto"><HistoryView ws={ws} /></TabsContent>

      {/* status bar */}
      <div className="flex h-7 shrink-0 items-center gap-3 border-t border-line-soft px-3 text-[10.5px] text-fg-3">
        {program ? (
          <span className="flex items-center gap-1">{program.author === 'agent' ? <Bot className="size-3" /> : <User className="size-3" />} v{program.version} · {program.author}{dirty && <span className="text-warn"> · unsaved edits</span>}</span>
        ) : <span>unsaved</span>}
        {run.running && <span className="flex items-center gap-1.5 text-accent"><Spinner className="size-3" /> expression {run.step ?? 0}/{run.total ?? '?'} · lines {run.line}{run.endLine !== run.line ? `–${run.endLine}` : ''}</span>}
        {!run.running && lastError && <span className="text-bad">error at line {lastError.line}</span>}
        <span className="ml-auto tabular">Ln {cursor.line}, Col {cursor.col}</span>
        <span className="hidden items-center gap-1 sm:flex"><Kbd>{mod}</Kbd><Kbd>↵</Kbd> run <Kbd>{mod}</Kbd><Kbd>S</Kbd> save</span>
      </div>
    </Tabs>
  )
}

function DiffView({ wid, ws, theme }: { wid: string; ws: WorkspaceState; theme: Theme }) {
  const versions = ws.snap?.programs ?? []
  const last = versions[versions.length - 1]?.version ?? 0
  const [pair, setPair] = useState<[number, number] | null>(null)
  const [a, b] = pair ?? [Math.max(1, last - 1), last]
  const [codes, setCodes] = useState<Record<number, Program>>({})
  useEffect(() => { setPair(null) }, [last])
  useEffect(() => {
    for (const v of [a, b]) if (v && !codes[v]) api.program(wid, v).then(p => setCodes(c => ({ ...c, [v]: p })))
  }, [a, b, wid, codes])
  if (versions.length < 2) return <Empty icon={<GitCompare />} title="No changes yet">Diffs appear once the program has two versions.</Empty>
  const meta = (v: number) => versions.find(x => x.version === v)
  const Sel = ({ v, on }: { v: number; on: (n: number) => void }) => (
    <select value={v} onChange={e => on(+e.target.value)} className="h-6 rounded border border-line bg-bg px-1.5 font-mono text-[11px] text-fg outline-none">
      {versions.map(x => <option key={x.version} value={x.version}>v{x.version} · {x.author}</option>)}
    </select>
  )
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line-soft px-3 py-1.5 text-[11px] text-fg-3">
        <Sel v={a} on={n => setPair([n, b])} /> → <Sel v={b} on={n => setPair([a, n])} />
        <span className="ml-2 truncate">{meta(b)?.summary}</span>
      </div>
      <div className="min-h-0 flex-1">
        {codes[a] && codes[b] ? (
          <DiffEditor key={theme + a + '-' + b} language="r" theme="tp" original={codes[a].code} modified={codes[b].code}
            options={{ ...OPTIONS, readOnly: true, renderSideBySide: true, useInlineViewWhenSpaceIsLimited: true, renderOverviewRuler: false }} />
        ) : <div className="m-4 h-32 rounded tp-shimmer" />}
      </div>
    </div>
  )
}

function ConsoleView({ ws, onLine }: { ws: WorkspaceState; onLine: (l: number) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const lines = ws.run.console
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }) }, [lines.length])
  if (!lines.length) return <Empty icon={<Terminal />} title="Nothing has run yet">Console output from the R sandbox appears here, live.</Empty>
  return (
    <div ref={ref} className="h-full overflow-auto bg-[var(--bg)] px-3 py-2 font-mono text-[11.5px] leading-[18px]">
      {lines.map((l, i) => {
        const m = l.text.match(/^(?:Error|Warning) \(lines? (\d+)/)
        const echo = l.stream === 'stdout' && /^[>+] /.test(l.text)
        return (
          <div key={i} className={cn('whitespace-pre-wrap break-words',
            l.stream === 'meta' && 'my-1 text-accent',
            l.stream === 'stderr' && (l.text.startsWith('Error') ? 'text-bad' : l.text.startsWith('Warning') ? 'text-warn' : l.text.startsWith('[tp]') ? 'text-ok' : 'text-fg-3'),
            echo && 'text-fg-3', l.stream === 'stdout' && !echo && 'text-fg')}>
            {m ? <button className="underline decoration-dotted underline-offset-2" onClick={() => onLine(+m[1])}>{l.text}</button> : l.text || ' '}
          </div>
        )
      })}
    </div>
  )
}

const KIND_LABEL: Record<string, string> = {
  user_message: 'User message', agent_start: 'Agent started', agent_end: 'Agent finished', tool_call: 'Tool call', tool_result: 'Tool result',
  program_version: 'Program version', run_start: 'Run started', run_end: 'Run finished', validation: 'Validation', spec_proposed: 'Spec proposed',
  spec_decided: 'Spec decision', signoff: 'Sign-off', dataset_added: 'Dataset imported', agent_error: 'Agent error', agent_cancelled: 'Cancelled',
  clarification: 'Question to user', assistant_message: 'Agent message', trace: 'Lineage traced', highlight: 'Code highlighted',
}

function HistoryView({ ws }: { ws: WorkspaceState }) {
  const [filter, setFilter] = useState<'all' | 'key'>('key')
  const KEY = new Set(['program_version', 'run_end', 'validation', 'spec_proposed', 'spec_decided', 'signoff', 'dataset_added'])
  const evs = useMemo(() => [...ws.events].reverse().filter(e => filter === 'all' || KEY.has(e.kind)), [ws.events, filter])
  return (
    <div className="p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] font-semibold text-fg-2">Audit trail</span>
        <span className="text-[11px] text-fg-3">{ws.events.length} events, append-only</span>
        <div className="ml-auto flex gap-1">
          {(['key', 'all'] as const).map(f => <button key={f} onClick={() => setFilter(f)} className={cn('rounded px-2 py-0.5 text-[11px]', filter === f ? 'bg-surface-3 text-fg' : 'text-fg-3')}>{f === 'key' ? 'Key events' : 'Everything'}</button>)}
        </div>
      </div>
      <ol className="relative ml-1.5 border-l border-line-soft">
        {evs.map(e => (
          <li key={e.id} className="relative pb-2.5 pl-4">
            <span className={cn('absolute -left-[4.5px] top-1.5 size-2 rounded-full border border-surface',
              e.kind.startsWith('spec') ? 'bg-violet' : e.kind === 'validation' ? (e.payload.tables?.some((t: { status: string }) => t.status === 'mismatch') ? 'bg-bad' : 'bg-ok') :
                e.kind === 'run_end' ? (e.payload.status === 'succeeded' ? 'bg-ok' : 'bg-bad') : e.kind === 'signoff' ? 'bg-warn' : 'bg-fg-3')} />
            <div className="flex items-baseline gap-2">
              <span className="text-[11.5px] font-medium text-fg-2">{KIND_LABEL[e.kind] ?? e.kind}</span>
              <span className="text-[10px] text-fg-3">{e.ts ? ago(e.ts) : ''}</span>
            </div>
            <div className="text-[11px] text-fg-3">{describe(e.kind, e.payload)}</div>
          </li>
        ))}
      </ol>
    </div>
  )
}

function describe(kind: string, p: Record<string, any>): string {
  switch (kind) {
    case 'program_version': return `v${p.version} by ${p.author}${p.previous ? ` (+${p.added} −${p.removed})` : ''}${p.summary ? ' · ' + p.summary : ''}`
    case 'run_end': return `${p.status} · program v${p.program_version} · ${p.duration}s · by ${p.by}`
    case 'validation': return (p.tables ?? []).map((t: any) => `${t.table_id}: ${t.passed} passed, ${t.mismatch} mismatch${t.not_verified ? `, ${t.not_verified} not verified` : ''}`).join('; ')
    case 'spec_proposed': return `v${p.version} by ${p.by}`
    case 'spec_decided': return `v${p.version} ${p.status}${p.note ? ' · ' + p.note : ''}`
    case 'signoff': return `${p.reviewer}: ${p.decision === 'approved' ? 'approved' : 'changes requested'} · program v${p.program_version} (${String(p.program_sha256).slice(0, 10)})${p.note ? ' · ' + p.note : ''}`
    case 'dataset_added': return `${p.name} · ${p.nrow} rows · ${String(p.sha256).slice(0, 10)}`
    case 'user_message': return p.text
    case 'tool_call': return p.name
    case 'tool_result': return `${p.name}: ${p.summary}`
    case 'agent_error': return p.message
    case 'assistant_message': return String(p.text).slice(0, 140)
    default: return ''
  }
}

