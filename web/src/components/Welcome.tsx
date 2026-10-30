import { motion } from 'motion/react'
import { ArrowRight, Database, FileCode2, FlaskConical, Moon, ShieldCheck, Sparkles, Sun, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api, type Health } from '@/lib/api'
import { ago } from '@/lib/utils'
import type { Theme } from '@/App'
import Dropzone from './Dropzone'
import { Logo } from './Logo'
import { Badge, Button, Input, Spinner } from './ui'

type WS = Awaited<ReturnType<typeof api.workspaces>>[number]

const STEPS = [
  { icon: Database, t: 'Import', d: 'CSV, XPT or RDS, parsed by R' },
  { icon: Workflow, t: 'Specify', d: 'You approve the analysis spec' },
  { icon: FileCode2, t: 'Program', d: 'The agent writes and runs R' },
  { icon: ShieldCheck, t: 'Verify', d: 'An independent engine checks every cell' },
]

export default function Welcome({ onOpen, theme, toggleTheme }: { onOpen: (id: string) => void; theme: Theme; toggleTheme: () => void }) {
  const [list, setList] = useState<WS[] | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState<'upload' | 'pilot' | null>(null)

  useEffect(() => {
    api.workspaces().then(setList).catch(() => setList([]))
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  const create = async (files?: File[]) => {
    setBusy('upload')
    try {
      const ws = await api.createWorkspace(name || (files?.length ? files.map(f => f.name.replace(/\.\w+$/, '').toUpperCase()).join(' + ') : 'Untitled analysis'))
      if (files?.length) {
        const r = await api.upload(ws.id, files)
        r.errors.forEach(e => toast.error(e.error))
        if (r.datasets.length) toast.success(`Imported ${r.datasets.map(d => d.name).join(', ')}`)
      }
      onOpen(ws.id)
    } catch (e) {
      toast.error(String((e as Error).message))
    } finally { setBusy(null) }
  }

  const pilot = async () => {
    setBusy('pilot')
    try {
      const ws = await api.createWorkspace(name || 'CDISC pilot · TEAE summary')
      await api.pilot(ws.id)
      onOpen(ws.id)
    } catch (e) {
      toast.error(String((e as Error).message))
    } finally { setBusy(null) }
  }

  return (
    <div className="bg-glow min-h-full bg-bg-deep">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-8">
        <div className="flex items-center gap-2.5"><Logo /><span className="text-[13px] font-semibold tracking-tight">TrialProof Studio</span></div>
        <div className="flex items-center gap-2">
          {health && (
            <Badge tone={health.llm.configured ? 'ok' : 'warn'} className="hidden sm:inline-flex">
              <span className="size-1.5 rounded-full bg-current" />
              {health.llm.configured ? `${health.llm.model} · ${health.llm.host}` : 'no model connected'}
            </Badge>
          )}
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">{theme === 'dark' ? <Sun /> : <Moon />}</Button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 pb-20 sm:px-8">
        <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }} className="pt-10 sm:pt-16">
          <Badge tone="accent" className="mb-5"><Sparkles /> AI statistical programming, independently checked</Badge>
          <h1 className="max-w-3xl text-[34px] font-semibold leading-[1.08] tracking-[-0.025em] text-fg sm:text-[46px]">
            Describe the analysis.<br />
            <span className="text-fg-3">Watch the R get written, run, and verified.</span>
          </h1>
          <p className="mt-5 max-w-2xl text-[14px] leading-relaxed text-fg-2">
            An agent inspects your datasets, proposes a specification for you to approve, writes R into a live editor, and runs it
            in a sandbox. A separate deterministic engine recomputes every number from the approved specification, cell by cell.
            You review and sign off.
          </p>
          <div className="mt-8 grid max-w-3xl grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
            {STEPS.map((s, i) => (
              <div key={s.t} className="bg-surface px-4 py-3.5">
                <div className="flex items-center gap-2 text-[12px] font-medium text-fg"><span className="font-mono text-[10px] text-fg-3">0{i + 1}</span><s.icon className="size-3.5 text-accent" />{s.t}</div>
                <div className="mt-1 text-[11.5px] leading-snug text-fg-3">{s.d}</div>
              </div>
            ))}
          </div>
        </motion.section>

        <motion.section initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.08 }} className="mt-12 grid gap-4 lg:grid-cols-[1.35fr_1fr]">
          {health?.public ? (
            <div className="rounded-2xl border border-line bg-surface p-5 shadow-panel backdrop-blur-xl">
              <div className="mb-1 text-[13px] font-semibold">Public demo</div>
              <div className="text-xs leading-relaxed text-fg-3">
                This copy runs on a shared public server with the public CDISC pilot study only. Uploading is turned off: do not
                put real participant data into a public demo. Runs and agent requests are rate-limited per visitor, and other
                visitors can open the workspaces listed here. The agent's model is {health.llm.configured ? `${health.llm.model} (${health.llm.host})` : 'not connected'}.
                It is not validated for regulatory submission or clinical decisions.
              </div>
            </div>
          ) : (
          <div className="rounded-2xl border border-line bg-surface p-5 shadow-panel backdrop-blur-xl">
            <div className="mb-1 text-[13px] font-semibold">New workspace</div>
            <div className="mb-4 text-xs text-fg-3">Bring your own analysis datasets. They stay on this machine; the R sandbox has no network access.</div>
            <Input placeholder="Workspace name (optional)" value={name} onChange={e => setName(e.target.value)} className="mb-3" />
            <Dropzone onFiles={create} busy={busy === 'upload'} />
            <div className="mt-3 flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => create()} disabled={!!busy}>Create empty workspace <ArrowRight /></Button>
            </div>
          </div>
          )}
          <div className="flex flex-col rounded-2xl border border-line bg-surface p-5 shadow-panel backdrop-blur-xl">
            <div className="mb-3 flex items-center gap-2.5">
              <div className="grid size-8 place-items-center rounded-lg bg-violet-soft text-violet"><FlaskConical className="size-4" /></div>
              <div>
                <div className="text-[13px] font-semibold">CDISC pilot study</div>
                <div className="text-[11px] text-fg-3">CDISCPILOT01 · ADSL + ADAE</div>
              </div>
            </div>
            <p className="text-xs leading-relaxed text-fg-2">
              The public ADaM datasets from the CDISC SDTM/ADaM pilot (Xanomeline vs placebo), published by PhUSE. Each file is
              checked against its SHA-256 before it is loaded.
            </p>
            <div className="mt-auto pt-5">
              <Button variant="primary" size="md" className="w-full" onClick={pilot} disabled={!!busy}>
                {busy === 'pilot' ? <><Spinner /> Loading and parsing…</> : <>Open pilot workspace <ArrowRight /></>}
              </Button>
            </div>
          </div>
        </motion.section>

        <section className="mt-12">
          <div className="mb-3 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-3">Recent workspaces</div>
          {list === null ? <div className="h-16 rounded-xl tp-shimmer" /> : list.length === 0 ? (
            <div className="rounded-xl border border-line-soft px-4 py-6 text-center text-xs text-fg-3">No workspaces yet.</div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {list.map(w => (
                <button key={w.id} onClick={() => onOpen(w.id)} className="group flex items-center justify-between rounded-xl border border-line bg-surface px-4 py-3 text-left transition hover:border-fg-3/50 hover:bg-surface-2">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium">{w.name}</div>
                    <div className="mt-0.5 text-[11px] text-fg-3">{w.datasets} dataset{w.datasets === 1 ? '' : 's'} · {ago(w.last_active ?? w.created_at)}</div>
                  </div>
                  <ArrowRight className="size-3.5 text-fg-3 transition group-hover:translate-x-0.5 group-hover:text-fg" />
                </button>
              ))}
            </div>
          )}
        </section>

        <footer className="mt-16 flex flex-wrap gap-x-6 gap-y-1 border-t border-line-soft pt-5 text-[11px] text-fg-3">
          <span>{health ? health.r : 'backend not reachable'}</span>
          {health && <span>R executor: {health.isolation ?? health.executor}</span>}
          <span>Not validated for regulatory submission or clinical decision-making.</span>
        </footer>
      </main>
    </div>
  )
}
