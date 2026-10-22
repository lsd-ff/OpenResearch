import { useEffect, useState } from 'react';

const clampWidth = (width: number) => Math.max(304, Math.min(width, window.innerWidth * 0.36, 560));

export function useScholarLayoutState() {
  const [sidebarWidth, setSidebarWidth] = useState(400);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  useEffect(() => {
    if (!isResizingSidebar) return;
    const previousCursor = document.body.style.cursor;
    document.body.style.cursor = 'col-resize';
    const move = (event: MouseEvent) => setSidebarWidth(clampWidth(window.innerWidth - event.clientX));
    const stop = () => setIsResizingSidebar(false);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', stop);
    return () => {
      window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', stop);
      document.body.style.cursor = previousCursor;
    };
  }, [isResizingSidebar]);
  return { sidebarWidth, isResizingSidebar, startResizingSidebar: () => setIsResizingSidebar(true),
    resizeSidebar: (delta: number) => setSidebarWidth(previous => clampWidth(previous + delta)) };
}
