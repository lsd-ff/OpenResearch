import { useEffect, useState } from 'react';

const clampWidth = (width: number) => Math.max(304, Math.min(width, window.innerWidth * 0.36, 560));
const WORKFLOW_WIDTH_KEY = 'openresearch-python.workflow-width.v1';

function savedWorkflowWidth(): number | null {
  try {
    const raw = localStorage.getItem(WORKFLOW_WIDTH_KEY);
    const width = raw ? Number(raw) : NaN;
    return Number.isFinite(width) && width >= 224 ? width : null;
  } catch { return null; }
}

export function useScholarLayoutState() {
  const [sidebarWidth, setSidebarWidth] = useState(400);
  const [workflowWidth, setWorkflowWidth] = useState<number | null>(savedWorkflowWidth);
  useEffect(() => {
    try {
      if (workflowWidth === null) localStorage.removeItem(WORKFLOW_WIDTH_KEY);
      else localStorage.setItem(WORKFLOW_WIDTH_KEY, String(workflowWidth));
    } catch { /* 禁用浏览器存储时仍允许本次页面调整宽度。 */ }
  }, [workflowWidth]);
  return { sidebarWidth, workflowWidth,
    resizeSidebar: (width: number) => setSidebarWidth(clampWidth(width)), resetSidebar: () => setSidebarWidth(400),
    resizeWorkflow: (width: number) => setWorkflowWidth(Math.max(224, Math.min(width, window.innerWidth))),
    resetWorkflow: () => setWorkflowWidth(null) };
}
