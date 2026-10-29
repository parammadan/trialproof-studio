import { useCallback, useEffect, useState } from 'react'
import Welcome from './components/Welcome'
import Workspace from './components/Workspace'
import { defineThemes } from './lib/monaco'

export type Theme = 'dark' | 'light'

const route = () => (location.hash.match(/^#\/w\/([a-z0-9]+)/)?.[1] ?? null)

export default function App() {
  const [wid, setWid] = useState<string | null>(route)
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.classList.contains('light') ? 'light' : 'dark'))

  useEffect(() => {
    const on = () => setWid(route())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light')
    try { localStorage.setItem('tp-theme', theme) } catch { /* storage unavailable */ }
    defineThemes()
  }, [theme])

  const toggleTheme = useCallback(() => setTheme(t => (t === 'dark' ? 'light' : 'dark')), [])
  const open = useCallback((id: string | null) => { location.hash = id ? `#/w/${id}` : '#/' }, [])

  return wid
    ? <Workspace key={wid} id={wid} theme={theme} toggleTheme={toggleTheme} onHome={() => open(null)} />
    : <Welcome onOpen={open} theme={theme} toggleTheme={toggleTheme} />
}
