import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Ev, type Run, type Snapshot, type Trace, type Validation } from './api'

export type ConsoleLine = { text: string; stream: 'stdout' | 'stderr' | 'meta' }
export type RunLive = {
  running: boolean; runId?: string; by?: string; programVersion?: number
  line?: number; endLine?: number; step?: number; total?: number; done: number[][]
  console: ConsoleLine[]
}

const REFRESH_ON = new Set(['program_version', 'spec_proposed', 'spec_decided', 'run_end', 'validation', 'dataset_added', 'signoff', 'agent_end', 'agent_start'])

/** One workspace: the snapshot from the API plus the live event stream (SSE) that keeps it current. */
export function useWorkspace(id: string) {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [events, setEvents] = useState<Ev[]>([])
  const [draft, setDraft] = useState('')
  const [codeStream, setCodeStream] = useState<{ callId: string; text: string } | null>(null)
  const [run, setRun] = useState<RunLive>({ running: false, done: [], console: [] })
  const [highlight, setHighlight] = useState<{ start: number; end: number; note: string; ts: number } | null>(null)
  const [trace, setTrace] = useState<Trace | null>(null)
  const [prevRun, setPrevRun] = useState<(Run & { validation: Validation | null }) | null>(null)
  const [connected, setConnected] = useState(false)
  const [activeTool, setActiveTool] = useState<string | null>(null)
  const refreshTimer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await api.snapshot(id)
      setSnap(s)
      setError(null)
      return s
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [id])

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) window.clearTimeout(refreshTimer.current)
    refreshTimer.current = window.setTimeout(refresh, 60)
  }, [refresh])

  // initial load
  useEffect(() => {
    let alive = true
    refresh().then(s => {
      if (!alive || !s?.run) return
      setRun(r => ({ ...r, console: s.run!.console.split('\n').filter(Boolean).map(text => ({ text, stream: 'stdout' as const })) }))
    })
    return () => { alive = false }
  }, [refresh])

  // previous run, for "changed since last run" highlighting
  const runId = snap?.run?.id
  useEffect(() => {
    if (!runId) { setPrevRun(null); return }
    let alive = true
    api.runs(id).then(rs => {
      const i = rs.findIndex(r => r.id === runId)
      const prev = rs.slice(i + 1).find(r => r.status === 'succeeded')
      if (!prev) { if (alive) setPrevRun(null); return }
      api.getRun(id, prev.id).then(p => alive && setPrevRun(p))
    })
    return () => { alive = false }
  }, [id, runId])

  // live events
  useEffect(() => {
    const es = new EventSource(api.eventsUrl(id, 0))
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.onmessage = m => {
      const ev: Ev = JSON.parse(m.data)
      const p = ev.payload
      switch (ev.kind) {
        case 'assistant_delta': setDraft(d => d + p.text); break
        case 'code_delta':
          setCodeStream(c => (c && c.callId === p.call_id ? { callId: c.callId, text: c.text + p.text } : { callId: p.call_id, text: p.text }))
          break
        case 'run_start':
          setRun({ running: true, runId: p.run_id, by: p.by, programVersion: p.program_version, done: [], console: [{ text: `▶ run ${p.run_id} · program v${p.program_version} · ${p.backend} sandbox · started by ${p.by}`, stream: 'meta' }] })
          break
        case 'run_step':
          if (p.total != null) setRun(r => ({ ...r, total: p.total }))
          else setRun(r => ({ ...r, step: p.step, line: p.line, endLine: p.end_line, done: r.line ? [...r.done, [r.line, r.endLine ?? r.line]] : r.done }))
          break
        case 'run_output':
          setRun(r => ({ ...r, console: [...r.console, ...String(p.text).replace(/\n$/, '').split('\n').map(text => ({ text, stream: p.stream }))] }))
          break
        case 'run_end':
          setRun(r => (r.runId === p.run_id
            ? { ...r, running: false, line: undefined, endLine: undefined, done: r.line && p.status === 'succeeded' ? [...r.done, [r.line, r.endLine ?? r.line]] : r.done, console: [...r.console, { text: `■ ${p.status} in ${p.duration}s`, stream: 'meta' }] }
            : r))
          break
        case 'tool_call_start': setActiveTool(p.name); break
        case 'tool_result': setActiveTool(null); break
        case 'assistant_message': setDraft(''); break
        case 'highlight': setHighlight({ start: p.start_line, end: p.end_line, note: p.note, ts: Date.now() }); break
        case 'trace': setTrace(p as Trace); break
        case 'agent_end': setDraft(''); setActiveTool(null); break
        case 'agent_cancelled': setDraft(''); setActiveTool(null); setCodeStream(null); break
      }
      if (ev.id != null) setEvents(es0 => (es0.length && es0[es0.length - 1].id! >= ev.id! ? es0 : [...es0, ev]))
      if (REFRESH_ON.has(ev.kind)) scheduleRefresh()
    }
    return () => es.close()
  }, [id, scheduleRefresh])

  return { snap, error, events, draft, codeStream, setCodeStream, run, highlight, setHighlight, trace, setTrace, prevRun, connected, activeTool, refresh }
}

export type WorkspaceState = ReturnType<typeof useWorkspace>
