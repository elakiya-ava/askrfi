import { useCallback, useState } from 'react'

function Upload({ onUpload, apiBase }) {
  const [isDragOver, setIsDragOver] = useState(false)
  const [error, setError] = useState('')
  const [isUploading, setIsUploading] = useState(false)

  const processFile = useCallback(async (file) => {
    setError('')

    if (!file.name.match(/\.(xlsx|xlsm|docx|pptx)$/i)) {
      setError('Please upload an RFI file (.xlsx, .xlsm, .docx, or .pptx)')
      return
    }

    setIsUploading(true)

    try {
      const formData = new FormData()
      formData.append('file', file)

      const res = await fetch(`${apiBase}/api/upload`, {
        method: 'POST',
        body: formData,
      })

      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(detail.detail || `Upload failed: ${res.status}`)
      }

      const data = await res.json()

      // Map backend questions to frontend shape
      const questions = data.questions.map((q, i) => ({
        id: `${q.sheet_name}-${q.row}`,
        sheet_name: q.sheet_name,
        row: q.row,
        question_text: q.question_text,
        existing_answer: q.existing_answer || '',
        status: 'pending',
        generated_answer: '',
        confidence: null,
        citation: '',
      }))

      onUpload(questions, data.file_name, data.client_name, data.session_id)
    } catch (err) {
      console.error('Upload error:', err)
      setError(err.message || 'Upload failed. Is the backend running?')
    } finally {
      setIsUploading(false)
    }
  }, [onUpload, apiBase])

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
        className={`drop-zone ${isDragOver ? 'drag-over' : ''} ${isUploading ? 'uploading' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setIsDragOver(true) }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        {isUploading ? (
          <>
            <div className="drop-icon">⏳</div>
            <h2>Parsing RFI...</h2>
            <p>Extracting questions from Excel file</p>
          </>
        ) : (
          <>
            <div className="drop-icon">📄</div>
            <h2>Upload RFI File</h2>
            <p>Drag & drop your .xlsx, .xlsm, .docx, or .pptx file here</p>
            <span className="or-divider">or</span>
            <label className="file-input-label">
              Browse Files
              <input
                type="file"
                accept=".xlsx,.xlsm,.docx,.pptx"
                onChange={handleFileInput}
                hidden
              />
            </label>
          </>
        )}
      </div>
      {error && <div className="upload-error">{error}</div>}
    </div>
  )
}

export default Upload
