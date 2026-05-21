import { useState } from 'react'
import Upload from './components/Upload'
import QuestionsTable from './components/QuestionsTable'
import './App.css'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function App() {
  const [questions, setQuestions] = useState(null)
  const [fileName, setFileName] = useState('')
  const [clientName, setClientName] = useState('')
  const [sessionId, setSessionId] = useState(null)
  const [isProcessing, setIsProcessing] = useState(false)

  const handleUpload = (parsedQuestions, name, client, sessId) => {
    setQuestions(parsedQuestions)
    setFileName(name)
    setClientName(client)
    setSessionId(sessId)
  }

  const handleStartFill = () => {
    if (!sessionId) return
    setIsProcessing(true)

    // Mark all as filling
    setQuestions(prev => prev.map(q => ({ ...q, status: 'filling' })))

    // Connect to SSE stream (use mock fill for demo)
    const evtSource = new EventSource(`${API_BASE}/api/fill-mock/${sessionId}`)

    evtSource.addEventListener('progress', (e) => {
      const data = JSON.parse(e.data)
      setQuestions(prev => {
        const updated = [...prev]
        const idx = data.index
        if (idx >= 0 && idx < updated.length) {
          updated[idx] = {
            ...updated[idx],
            status: data.status === 'filled' ? 'filled' : data.status === 'pending' ? 'filling' : 'error',
            generated_answer: data.generated_answer || '',
            confidence: data.confidence,
            citation: data.citation || '',
          }
        }
        return updated
      })
    })

    evtSource.addEventListener('done', () => {
      evtSource.close()
      setIsProcessing(false)
    })

    evtSource.addEventListener('error', () => {
      evtSource.close()
      setIsProcessing(false)
    })
  }

  const handleReview = async () => {
    if (!sessionId) return
    setIsProcessing(true)
    try {
      const res = await fetch(`${API_BASE}/api/review/${sessionId}`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setQuestions(data.questions.map(q => ({
        ...q,
        status: q.fill_status === 'filled' ? 'filled' : q.fill_status || 'pending',
      })))
    } catch (err) {
      console.error('Review failed:', err)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleDownload = async (format = 'excel') => {
    if (!sessionId) return
    try {
      const endpoint = format === 'csv'
        ? `${API_BASE}/api/download-csv/${sessionId}`
        : `${API_BASE}/api/download/${sessionId}`
      const res = await fetch(endpoint)
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Download failed' }))
        alert(err.detail || 'Download failed')
        return
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      if (format === 'csv') {
        a.download = fileName.replace(/\.(xlsx|xlsm)$/i, '') + '_FILLED.csv'
      } else {
        const ext = fileName.match(/\.(xlsx|xlsm)$/i)?.[0] || '.xlsx'
        a.download = fileName.replace(/\.(xlsx|xlsm)$/i, '') + '_FILLED' + ext
      }
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      alert('Download failed — is the backend running?')
    }
  }

  const handleReset = () => {
    setQuestions(null)
    setFileName('')
    setClientName('')
    setSessionId(null)
    setIsProcessing(false)
  }

  const allFilled = questions && questions.every(q => q.status === 'filled')

  return (
    <div className="app">
      <header className="app-header">
        <h1>RFI Agent</h1>
        <span className="subtitle">Auto-fill RFIs for Avalere Health</span>
      </header>

      {!questions ? (
        <Upload onUpload={handleUpload} apiBase={API_BASE} />
      ) : (
        <div className="workspace">
          <div className="toolbar">
            <div className="file-info">
              <span className="file-name">{fileName}</span>
              {clientName && <span className="client-badge">{clientName}</span>}
              <span className="q-count">{questions.length} questions</span>
            </div>
            <div className="toolbar-actions">
              {!isProcessing && questions.some(q => q.status === 'pending') && (
                <button className="btn-fill" onClick={handleStartFill}>
                  ▶ Fill with AI
                </button>
              )}
              {!isProcessing && allFilled && (
                <>
                  <button className="btn-download" onClick={() => handleDownload('excel')}>
                    ⬇ Download Excel
                  </button>
                  <button className="btn-download btn-csv" onClick={() => handleDownload('csv')}>
                    ⬇ Download CSV
                  </button>
                </>
              )}
              <button className="btn-reset" onClick={handleReset}>
                ✕ New File
              </button>
            </div>
          </div>
          <QuestionsTable questions={questions} />
        </div>
      )}
    </div>
  )
}

export default App
