import { useCallback, useState } from 'react'
import * as XLSX from 'xlsx'

const KNOWN_CLIENTS = [
  'Pfizer', 'Gilead', 'AbbVie', 'AstraZeneca', 'AZ', 'Novartis',
  'Servier', 'UCB', 'GSK', 'Chiesi', 'Rigel', 'Exact Sciences',
]

function extractClient(filename) {
  const lower = filename.toLowerCase()
  for (const client of KNOWN_CLIENTS) {
    if (lower.includes(client.toLowerCase())) {
      return client === 'AZ' ? 'AstraZeneca' : client
    }
  }
  return ''
}

function Upload({ onUpload }) {
  const [isDragOver, setIsDragOver] = useState(false)
  const [error, setError] = useState('')

  const processFile = useCallback((file) => {
    setError('')

    if (!file.name.match(/\.(xlsx|xlsm)$/i)) {
      setError('Please upload an Excel file (.xlsx or .xlsm)')
      return
    }

    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target.result)
        const workbook = XLSX.read(data, { type: 'array', bookVBA: true })

        const questions = []
        workbook.SheetNames.forEach(sheetName => {
          const sheet = workbook.Sheets[sheetName]
          if (!sheet || !sheet['!ref']) return // Skip empty sheets
          const json = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' })

          if (json.length < 2) return // Skip tiny sheets

          // Find question/answer columns by header text
          let qCol = -1
          let aCol = -1

          // Check first few rows for headers
          for (let headerIdx = 0; headerIdx < Math.min(5, json.length); headerIdx++) {
            const headerRow = json[headerIdx] || []
            for (let c = 0; c < headerRow.length; c++) {
              const h = String(headerRow[c] || '').toLowerCase().trim()
              if (qCol === -1 && h.match(/question|query|requirement|description|item/)) qCol = c
              if (aCol === -1 && h.match(/answer|response|reply|vendor|supplier/)) aCol = c
            }
            if (qCol !== -1) break
          }

          // Fallback: find column with most total text content
          if (qCol === -1) {
            const maxCols = Math.max(...json.map(r => (r || []).length))
            const colLengths = Array.from({ length: maxCols }, (_, c) =>
              json.slice(0).reduce((sum, row) => sum + String((row || [])[c] || '').length, 0)
            )
            if (colLengths.length > 0) {
              qCol = colLengths.indexOf(Math.max(...colLengths))
            }
          }

          if (qCol === -1) return // No usable column found

          // Extract questions from rows
          for (let r = 0; r < json.length; r++) {
            const row = json[r] || []
            const qText = String(row[qCol] || '').trim()
            if (qText.length < 15) continue // Skip short/empty/header rows

            questions.push({
              id: `${sheetName}-${r}`,
              sheet_name: sheetName,
              row: r + 1,
              question_text: qText,
              existing_answer: aCol >= 0 ? String(row[aCol] || '').trim() : '',
              status: 'pending',
              generated_answer: '',
              confidence: null,
              citation: '',
            })
          }
        })

        if (questions.length === 0) {
          setError(`No questions found in this file (${workbook.SheetNames.length} sheets detected: ${workbook.SheetNames.join(', ')}). Check the Excel format.`)
          return
        }

        const client = extractClient(file.name)
        onUpload(questions, file.name, client)
      } catch (err) {
        console.error('Excel parse error:', err)
        setError(`Failed to parse Excel: ${err.message}`)
      }
    }
    reader.onerror = () => {
      setError('Failed to read the file. Please try again.')
    }
    reader.readAsArrayBuffer(file)
  }, [onUpload])

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) processFile(file)
  }

  const handleFileInput = (e) => {
    const file = e.target.files[0]
    if (file) processFile(file)
  }

  return (
    <div className="upload-section">
      <div
        className={`drop-zone ${isDragOver ? 'drag-over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setIsDragOver(true) }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        <div className="drop-icon">📄</div>
        <h2>Upload RFI Excel File</h2>
        <p>Drag & drop your .xlsx or .xlsm file here</p>
        <span className="or-divider">or</span>
        <label className="file-input-label">
          Browse Files
          <input
            type="file"
            accept=".xlsx,.xlsm"
            onChange={handleFileInput}
            hidden
          />
        </label>
      </div>
      {error && <div className="upload-error">{error}</div>}
    </div>
  )
}

export default Upload
