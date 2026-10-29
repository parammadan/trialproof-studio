// Monaco is bundled locally (no CDN). R syntax comes from Monaco's built-in R grammar.
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import EditorWorker from 'monaco-editor/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/language/json/json.worker?worker'

self.MonacoEnvironment = {
  getWorker: (_id: string, label: string) => (label === 'json' ? new JsonWorker() : new EditorWorker()),
}
loader.config({ monaco })

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()

/** Monaco themes need hex colors; read our tokens through a canvas so they track the CSS. */
function hex(color: string) {
  const c = document.createElement('canvas').getContext('2d')!
  c.fillStyle = color
  c.fillRect(0, 0, 1, 1)
  const [r, g, b] = c.getImageData(0, 0, 1, 1).data
  return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('')
}

export function defineThemes() {
  const light = document.documentElement.classList.contains('light')
  const bg = hex(css('--bg'))
  const fg = hex(css('--fg'))
  const fg3 = hex(css('--fg-3'))
  const accent = hex(css('--accent'))
  monaco.editor.defineTheme('tp', {
    base: light ? 'vs' : 'vs-dark',
    inherit: true,
    rules: light
      ? [
          { token: 'comment', foreground: '7c8696', fontStyle: 'italic' },
          { token: 'string', foreground: '0f7b5f' },
          { token: 'number', foreground: 'a04a00' },
          { token: 'keyword', foreground: '6d3fc9' },
          { token: 'identifier', foreground: '1f2430' },
          { token: 'operator', foreground: '2e5bd6' },
          { token: 'delimiter', foreground: '6b7280' },
        ]
      : [
          { token: 'comment', foreground: '6c7689', fontStyle: 'italic' },
          { token: 'string', foreground: '7fe0bf' },
          { token: 'number', foreground: 'f5b97a' },
          { token: 'keyword', foreground: 'c3a6ff' },
          { token: 'identifier', foreground: 'dfe4ee' },
          { token: 'operator', foreground: '8fb8ff' },
          { token: 'delimiter', foreground: '8b93a5' },
        ],
    colors: {
      'editor.background': bg,
      'editor.foreground': fg,
      'editorLineNumber.foreground': fg3 + '90',
      'editorLineNumber.activeForeground': fg,
      'editorCursor.foreground': accent,
      'editor.selectionBackground': accent + '40',
      'editor.lineHighlightBackground': light ? '#00000006' : '#ffffff07',
      'editor.lineHighlightBorder': '#00000000',
      'editorGutter.background': bg,
      'editorIndentGuide.background1': light ? '#0000000f' : '#ffffff0d',
      'editorWidget.background': hex(css('--surface-3')),
      'diffEditor.insertedTextBackground': light ? '#16a34a22' : '#34d39924',
      'diffEditor.removedTextBackground': light ? '#dc262620' : '#f8717124',
      'diffEditor.insertedLineBackground': light ? '#16a34a12' : '#34d39912',
      'diffEditor.removedLineBackground': light ? '#dc262610' : '#f8717112',
      'scrollbarSlider.background': light ? '#00000018' : '#ffffff14',
    },
  })
  monaco.editor.setTheme('tp')
}

export { monaco }
