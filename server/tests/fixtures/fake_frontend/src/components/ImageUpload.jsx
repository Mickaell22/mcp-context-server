import { useRef, useState } from 'react'

export default function ImageUpload({ imageUrl, onUpload, onDelete }) {
  const inputRef = useRef(null)
  const [hover, setHover] = useState(false)

  if (imageUrl) {
    return (
      <div className="thumb" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        {/* BUG SEMBRADO (accessibility): <img> sin atributo alt descriptivo */}
        <img src={imageUrl} className="w-12 h-12 rounded" />

        {/* BUG SEMBRADO (accessibility): boton de solo icono sin aria-label */}
        <button onClick={onDelete}>&times;</button>

        <input ref={inputRef} type="file" accept="image/*" className="hidden"
          onChange={(e) => e.target.files[0] && onUpload(e.target.files[0])} />
      </div>
    )
  }

  // BUG SEMBRADO (accessibility): div con onClick sin role="button" ni tabIndex,
  // no es navegable por teclado ni se anuncia en screen readers.
  return (
    <div className="dropzone" onClick={() => inputRef.current.click()}>
      <span>subir foto</span>
      <input ref={inputRef} type="file" accept="image/*" className="hidden"
        onChange={(e) => e.target.files[0] && onUpload(e.target.files[0])} />
    </div>
  )
}
