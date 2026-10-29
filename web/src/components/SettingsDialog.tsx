import { CircleCheck, CircleX } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api, type Health } from '@/lib/api'
import { Button, Dialog, Input, Spinner } from './ui'

const PRESETS = [
  { name: 'vLLM on NU Explorer (SSH tunnel)', base_url: 'http://127.0.0.1:8100/v1', model: 'qwen2.5-coder-32b' },
  { name: 'Local OpenAI-compatible server', base_url: 'http://127.0.0.1:8000/v1', model: '' },
]

export default function SettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [h, setH] = useState<Health | null>(null)
  const [base, setBase] = useState('')
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const [test, setTest] = useState<null | 'busy' | { ok: boolean; text: string; models?: string[] }>(null)

  useEffect(() => {
    if (!open) return
    api.health().then(x => { setH(x); setBase(x.llm.base_url); setModel(x.llm.model); setKey('') })
    setTest(null)
  }, [open])

  const save = async () => {
    await api.setLLM({ base_url: base, model, api_key: key ? key : null })
    toast.success('Model endpoint saved')
  }
  const check = async () => {
    setTest('busy')
    await save()
    const r = await api.testLLM()
    setTest(r.ok ? { ok: !!r.model_available, models: r.models, text: r.model_available ? `Connected. ${model} is served.` : `Connected, but "${model}" is not among the served models.` } : { ok: false, text: r.error ?? 'failed' })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Model endpoint" description="Any OpenAI-compatible chat-completions server with tool calling: vLLM serving an open-weight model on a GPU, llama.cpp, or a hosted API.">
      <div className="space-y-3 p-5">
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map(p => <Button key={p.name} size="xs" variant="outline" onClick={() => { setBase(p.base_url); if (p.model) setModel(p.model) }}>{p.name}</Button>)}
        </div>
        <label className="block text-[11px] text-fg-3">Base URL<Input value={base} onChange={e => setBase(e.target.value)} placeholder="http://127.0.0.1:8100/v1" className="mt-1 font-mono text-xs" /></label>
        <label className="block text-[11px] text-fg-3">Model<Input value={model} onChange={e => setModel(e.target.value)} placeholder="served model name" className="mt-1 font-mono text-xs" /></label>
        <label className="block text-[11px] text-fg-3">API key {h?.llm.has_key && <span className="text-fg-2">(one is saved; leave blank to keep it)</span>}<Input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="optional" className="mt-1 font-mono text-xs" /></label>
        {test && test !== 'busy' && (
          <div className={`flex items-start gap-2 rounded-md px-2.5 py-2 text-[11.5px] ${test.ok ? 'bg-ok-soft text-ok' : 'bg-bad-soft text-bad'}`}>
            {test.ok ? <CircleCheck className="mt-0.5 size-3.5" /> : <CircleX className="mt-0.5 size-3.5" />}
            <div className="min-w-0 break-words">{test.text}{test.models && !test.ok && <div className="mt-1 font-mono text-[10.5px] text-fg-2">served: {test.models.join(', ')}</div>}</div>
          </div>
        )}
        <div className="rounded-md bg-surface-3/60 px-2.5 py-2 text-[11px] leading-relaxed text-fg-3">
          The first message to a new host asks you to authorize what is sent (metadata, value counts, code, result previews; never whole datasets).
          {h && h.acked_hosts.length > 0 && <> Authorized: <span className="font-mono text-fg-2">{h.acked_hosts.join(', ')}</span>.</>}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
          <Button onClick={check} disabled={!base || test === 'busy'}>{test === 'busy' ? <Spinner /> : null} Save & test</Button>
          <Button variant="primary" onClick={async () => { await save(); onOpenChange(false) }} disabled={!base || !model}>Save</Button>
        </div>
      </div>
    </Dialog>
  )
}
