import { useEffect, useRef, useState, type PointerEvent } from 'react';

interface Props {
  label: string; targetId: string; minimum: number;
  onResize: (width: number) => void; onReset: () => void;
}

export function ColumnResizeHandle({ label, targetId, minimum, onResize, onReset }: Props) {
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  const [width, setWidth] = useState(minimum);
  const [dragging, setDragging] = useState(false);
  const panelWidth = () => document.getElementById(targetId)?.getBoundingClientRect().width ?? minimum;
  useEffect(() => {
    const panel = document.getElementById(targetId);
    if (!panel) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0]?.contentRect.width ?? minimum));
    observer.observe(panel);
    return () => observer.disconnect();
  }, [targetId, minimum]);
  const stop = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null; setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  return <div className="column-resize-handle" role="separator" aria-label={label} aria-orientation="vertical"
    aria-controls={targetId} aria-valuemin={minimum} aria-valuemax={window.innerWidth}
    aria-valuenow={Math.max(minimum, Math.round(width))} aria-valuetext={`${Math.round(width)} 像素`}
    tabIndex={0} title="左右拖动调整宽度，双击恢复默认" data-resizing={dragging}
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag.current = { pointerId: event.pointerId, x: event.clientX, width: panelWidth() };
      // 捕获指针，拖动经过流程画布或离开分隔条时也能继续，并在松开时可靠结束。
      event.currentTarget.setPointerCapture(event.pointerId); setDragging(true);
    }}
    onPointerMove={event => {
      const start = drag.current;
      if (start?.pointerId === event.pointerId) onResize(start.width + start.x - event.clientX);
    }} onPointerUp={stop} onPointerCancel={stop}
    onLostPointerCapture={() => { drag.current = null; setDragging(false); }}
    onDoubleClick={onReset}
    onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
      event.preventDefault();
      onResize(event.key === 'Home' ? minimum : panelWidth() + (event.key === 'ArrowLeft' ? 20 : -20));
    }} />;
}
