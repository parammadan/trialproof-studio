import { ChevronLeft, Download, Moon, PanelBottom, PanelLeft, Play, Settings2, Square, Sun } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Group, Panel, Separator, usePanelRef } from 'react-resizable-panels'
import { toast } from 'sonner'
import type { Theme } from '@/App'
import { api, type Cell, type Health } from '@/lib/api'
import { useWorkspace } from '@/lib/useWorkspace'
import { cn, mod } from '@/lib/utils'
import AgentPanel from './AgentPanel'
import DataExplorer from './DataExplorer'
import EditorPane from './EditorPane'
import { Logo } from './Logo'
import ResultsPane from './ResultsPane'
import SettingsDialog from './SettingsDialog'
import { Badge, Button, Kbd, Spinner, Tip } from './ui'

export type CenterTab = 'editor' | 'diff' | 'console' | 'spec' | 'history'
export type RightTab = 'results' | 'verify' | 'lineage'
export type CellRef = { table_id: string; level: string; soc: string | null; pt: string | null; group: string; n?: unknown; N?: unknown; status?: Cell['status'] }

export default function Workspace({ id, theme, toggleTheme, onHome }: { id: string; theme: Theme; toggleTheme: () => void; onHome: () => void }) {
  const ws = useWorkspace(id)
  const { snap } = ws
  const [code, setCode] = useState('')
  const [baseVersion, setBaseVersion] = useState<number | null>(null)
  const [center, setCenter] = useState<CenterTab>('editor')
  const [right, setRight] = useState<RightTab>('results')
  const [cell, setCell] = useState<CellRef | null>(null)
  const [settings, setSettings] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [running, setRunning] = useState(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const [narrow, setNarrow] = useState(() => window.innerWidth < 900)
  const [pane, setPane] = useState<'data' | 'code' | 'agent' | 'results'>('agent')
  useEffect(() => {
    const on = () => setNarrow(window.innerWidth < 900)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  const dataPanel = usePanelRef()
  const agentPanel = usePanelRef()

  const loadHealth = useCallback(() => api.health().then(setHealth).catch(() => setHealth(null)), [])
  useEffect(() => { loadHealth() }, [loadHealth])

  // The editor buffer follows the latest saved program unless the user has unsaved edits
  const latest = snap?.program
  const savedCode = latest?.code ?? ''
  const dirty = latest ? code !== savedCode : code.trim().length > 0
  useEffect(() => {
    if (!latest) return
    if (baseVersion === null || latest.version !== baseVersion) {
      setCode(latest.code)
      setBaseVersion(latest.version)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest?.version])

  const save = useCallback(async () => {
    if (!dirty) return snap?.program?.version
    const p = await api.saveProgram(id, code)
    setBaseVersion(p.version)
    toast.success(`Saved program v${p.version}`)
    return p.version
  }, [dirty, code, id, snap?.program?.version])

  const runNow = useCallback(async () => {
    if (running || ws.run.running) return
    try {
      await save()
      setRunning(true)
      setCenter(c => (c === 'diff' || c === 'history' || c === 'spec' ? 'editor' : c))
      const r = await api.run(id)
      if (r.status !== 'succeeded') toast.error(`Run ${r.status}`)
      else setRight('results')
    } catch (e) {
      toast.error(String((e as Error).message))
    } finally { setRunning(false) }
  }, [running, ws.run.running, save, id])

  const send = useCallback(async (text: string, context?: Record<string, unknown>) => {
    try {
      await save()
      await api.message(id, text, context)
    } catch (e) {
      toast.error(String((e as Error).message))
    }
  }, [id, save])

  const cancel = useCallback(async () => {
    const r = await api.cancel(id) as { agent_cancelled: boolean; runs_cancelled: number }
    if (r.agent_cancelled || r.runs_cancelled) toast('Cancelled')
  }, [id])

  const trace = useCallback(async (c: CellRef) => {
    if (!snap?.run) return
    try {
      ws.setTrace(await api.trace(id, { run_id: snap.run.id, table_id: c.table_id, level: c.level, soc: c.soc, pt: c.pt, group: c.group }))
      setRight('lineage')
    } catch (e) { toast.error(String((e as Error).message)) }
  }, [id, snap?.run, ws])

  const reveal = useCallback((start: number, end: number, note: string) => {
    ws.setHighlight({ start, end, note, ts: Date.now() })
    setCenter('editor')
  }, [ws])

  // keyboard shortcuts
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const m = e.metaKey || e.ctrlKey
      if (!m) return
      if (e.key === 'Enter' && !e.shiftKey && document.activeElement !== composerRef.current) { e.preventDefault(); runNow() }
      else if (e.key === 's') { e.preventDefault(); save() }
      else if (e.key === 'k') { e.preventDefault(); agentPanel.current?.expand(); composerRef.current?.focus() }
      else if (e.key === '.') { e.preventDefault(); cancel() }
      else if (e.key === 'b') { e.preventDefault(); const p = dataPanel.current; if (p) { if (p.isCollapsed()) p.expand(); else p.collapse() } }
      else if (e.key === 'j') { e.preventDefault(); const p = agentPanel.current; if (p) { if (p.isCollapsed()) p.expand(); else p.collapse() } }
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [runNow, save, cancel, dataPanel, agentPanel])

  if (ws.error && !snap) {
    return (
      <div className="grid h-full place-items-center bg-bg-deep text-center">
        <div><div className="text-sm text-fg-2">{ws.error}</div><Button className="mt-4" onClick={onHome}>Back to workspaces</Button></div>
      </div>
    )
  }
  const busy = !!snap?.agent_busy
  const isRunning = running || ws.run.running

  return (
    <div className="bg-glow flex h-full flex-col bg-bg-deep">
      {/* top bar */}
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line-soft px-2.5">
        <Tip content="All workspaces"><Button variant="ghost" size="icon" onClick={onHome} aria-label="All workspaces"><ChevronLeft /></Button></Tip>
        <Logo size={20} />
        <div className="min-w-0 max-w-[30vw] sm:max-w-none">
          <div className="truncate text-[13px] font-semibold leading-tight">{snap?.workspace.name ?? '…'}</div>
        </div>
        <div className="ml-2 hidden items-center gap-1.5 md:flex">
          {snap?.specs.some(s => s.status === 'approved') && <Badge tone="outline">spec v{[...snap.specs].reverse().find(s => s.status === 'approved')!.version}</Badge>}
          {snap?.program && <Badge tone="outline">program v{snap.program.version}</Badge>}
          {ws.connected ? null : <Badge tone="warn">reconnecting…</Badge>}
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <Tip content={<span>Run program <Kbd>{mod}</Kbd><Kbd>↵</Kbd></span>}>
            <Button variant="primary" size="sm" onClick={runNow} disabled={!code.trim() || isRunning}>
              {isRunning ? <Spinner /> : <Play />} {isRunning ? 'Running' : 'Run'}
            </Button>
          </Tip>
          {(busy || isRunning) && (
            <Tip content={<span>Stop agent and runs <Kbd>{mod}</Kbd><Kbd>.</Kbd></span>}>
              <Button variant="danger" size="sm" onClick={cancel}><Square /> Stop</Button>
            </Tip>
          )}
          <Tip content="Download a reproducible bundle: program, spec, results, validation, provenance and audit log">
            <Button variant="ghost" size="sm" asChild={!!snap?.run} disabled={!snap?.run}>
              {snap?.run ? <a href={api.exportUrl(id, snap.run.id)}><Download /> Export</a> : <span><Download /> Export</span>}
            </Button>
          </Tip>
          <div className="mx-1 h-5 w-px bg-line" />
          <button onClick={() => setSettings(true)} className="flex h-7 items-center gap-1.5 rounded-md border border-line px-2 text-[11px] text-fg-2 transition hover:bg-surface-3">
            <span className={cn('size-1.5 rounded-full', health?.llm.configured ? 'bg-ok' : 'bg-warn')} />
            <span className="hidden max-w-40 truncate sm:inline">{health?.llm.configured ? health.llm.model : 'Connect a model'}</span>
            <Settings2 className="size-3.5 text-fg-3" />
          </button>
          {!narrow && <Tip content={<span>Toggle data panel <Kbd>{mod}</Kbd><Kbd>B</Kbd></span>}><Button variant="ghost" size="icon" onClick={() => { const p = dataPanel.current; if (p) { if (p.isCollapsed()) p.expand(); else p.collapse() } }}><PanelLeft /></Button></Tip>}
          {!narrow && <Tip content={<span>Toggle agent panel <Kbd>{mod}</Kbd><Kbd>J</Kbd></span>}><Button variant="ghost" size="icon" onClick={() => { const p = agentPanel.current; if (p) { if (p.isCollapsed()) p.expand(); else p.collapse() } }}><PanelBottom /></Button></Tip>}
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">{theme === 'dark' ? <Sun /> : <Moon />}</Button>
        </div>
      </header>

      {narrow ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 p-1.5">
            <Shell>
              {pane === 'data' && <DataExplorer wid={id} ws={ws} />}
              {pane === 'code' && <EditorPane wid={id} ws={ws} code={code} setCode={setCode} dirty={dirty} tab={center} setTab={setCenter} theme={theme} onRun={runNow} onSave={save} send={send} />}
              {pane === 'agent' && <AgentPanel wid={id} ws={ws} send={send} cancel={cancel} cell={cell} clearCell={() => setCell(null)} composerRef={composerRef} health={health}
                  onSettings={() => setSettings(true)} onAck={loadHealth} onReveal={reveal} openSpec={() => setCenter('spec')} openDiff={() => setCenter('diff')} />}
              {pane === 'results' && <ResultsPane wid={id} ws={ws} tab={right} setTab={setRight} cell={cell} setCell={setCell} onTrace={trace} send={send} onReveal={reveal} />}
            </Shell>
          </div>
          <nav className="grid shrink-0 grid-cols-4 border-t border-line-soft">
            {(['data', 'code', 'agent', 'results'] as const).map(p => (
              <button key={p} onClick={() => setPane(p)} className={cn('h-11 text-[12px] capitalize', pane === p ? 'text-fg' : 'text-fg-3')}>{p}</button>
            ))}
          </nav>
        </div>
      ) : (
      <Group orientation="horizontal" className="min-h-0 flex-1 p-1.5" id="tp-main">
        <Panel id="data" panelRef={dataPanel} defaultSize="19" minSize="200px" collapsible collapsedSize="0px" className="min-w-0">
          <Shell><DataExplorer wid={id} ws={ws} /></Shell>
        </Panel>
        <Separator />
        <Panel id="center" defaultSize="46" minSize="320px" className="min-w-0">
          <Group orientation="vertical" id="tp-center">
            <Panel id="editor" defaultSize="58" minSize="160px">
              <Shell>
                <EditorPane wid={id} ws={ws} code={code} setCode={setCode} dirty={dirty} tab={center} setTab={setCenter} theme={theme} onRun={runNow} onSave={save} send={send} />
              </Shell>
            </Panel>
            <Separator />
            <Panel id="agent" panelRef={agentPanel} defaultSize="42" minSize="140px" collapsible collapsedSize="0px">
              <Shell>
                <AgentPanel wid={id} ws={ws} send={send} cancel={cancel} cell={cell} clearCell={() => setCell(null)} composerRef={composerRef} health={health}
                  onSettings={() => setSettings(true)} onAck={loadHealth} onReveal={reveal} openSpec={() => setCenter('spec')} openDiff={() => setCenter('diff')} />
              </Shell>
            </Panel>
          </Group>
        </Panel>
        <Separator />
        <Panel id="right" defaultSize="35" minSize="300px" className="min-w-0">
          <Shell>
            <ResultsPane wid={id} ws={ws} tab={right} setTab={setRight} cell={cell} setCell={setCell} onTrace={trace} send={send} onReveal={reveal} />
          </Shell>
        </Panel>
      </Group>
      )}

      <SettingsDialog open={settings} onOpenChange={o => { setSettings(o); if (!o) loadHealth() }} />
    </div>
  )
}

const Shell = ({ children }: { children: React.ReactNode }) => (
  <div className="m-[3px] flex h-[calc(100%-6px)] flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-panel backdrop-blur-xl">{children}</div>
)
