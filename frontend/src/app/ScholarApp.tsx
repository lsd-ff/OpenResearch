import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useEdgesState, useNodesState, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import 'katex/dist/katex.min.css';
import '@react-pdf-viewer/core/lib/styles/index.css';
import '@react-pdf-viewer/default-layout/lib/styles/index.css';
import { ScholarRuntimeProvider } from './context/ScholarRuntimeProvider';
import type { ScholarRuntimeContextValue } from './context/ScholarRuntimeContext';
import { usePdfAssistFlow } from './hooks/usePdfAssistFlow';
import { useScholarChatFlow } from './hooks/useScholarChatFlow';
import { useScholarLayoutState } from './hooks/useScholarLayoutState';
import { useScholarRuntime } from './hooks/useScholarRuntime';
import { buildGraphLayout } from '../features/plan-graph/buildGraphLayout';
import type { PlanGraph } from '../contracts/api';
import { ResearchWorkspace } from '../features/research/ResearchWorkspace';
import { API_BASE_URL } from '../config/env';
import { WorkbenchHeader } from './components/WorkbenchHeader';
import { ScholarWorkspace } from './components/ScholarWorkspace';
import '../features/research/research.css';
import './workbench.css';

export default function ScholarApp() {
  const [researchOpen, setResearchOpen] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [mobilePane, setMobilePane] = useState<'chat' | 'graph'>('chat');
  const logsEndRef = useRef<HTMLDivElement>(null);
  const layout = useScholarLayoutState();
  const handlePlanGraphChanged = useCallback((planGraph: PlanGraph | null) => {
    if (!planGraph) { setNodes([]); setEdges([]); setMobilePane('chat'); return; }
    const graphLayout = buildGraphLayout(planGraph);
    setNodes(previous => {
      const existing = new Map(previous.map(node => [node.id, node]));
      // 状态刷新保留尺寸和选中态，避免重新测量闪烁或丢失当前节点的高亮。
      return graphLayout.nodes.map(node => ({ ...node, measured: existing.get(node.id)?.measured, selected: existing.get(node.id)?.selected }));
    });
    setEdges(graphLayout.edges);
    setMobilePane('graph');
  }, [setEdges, setNodes]);
  const chatFlow = useScholarChatFlow({ onPlanGraphChanged: handlePlanGraphChanged });
  const runtime = useScholarRuntime({ activePlanId: chatFlow.activePlanId, nodes, setNodes,
    appendChatMessage: chatFlow.appendChatMessage, identity: chatFlow.requestIdentity });
  const pdfFlow = usePdfAssistFlow({ identity: chatFlow.requestIdentity, setPrompt: chatFlow.setPrompt,
    appendChatMessage: chatFlow.appendChatMessage, appendSelectedTaskLog: runtime.appendSelectedTaskLog });
  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [runtime.selectedTaskState.logs]);
  const context = useMemo<ScholarRuntimeContextValue>(() => ({
    state: { executionState: runtime.executionState, selectedTaskState: runtime.selectedTaskState },
    actions: { onNodeClick: runtime.onNodeClick, handleOpenTaskView: runtime.handleOpenTaskView,
      handleExecuteTask: runtime.handleExecuteTask, handleRunAllTasks: runtime.handleRunAllTasks,
      handleApproveAndRun: runtime.handleApproveAndRun, handleCancelPlan: runtime.handleCancelPlan,
      handleRetryFailedPlan: runtime.handleRetryFailedPlan, setDisplayMode: runtime.setDisplayMode,
      closeTaskPanel: runtime.closeTaskPanel, resetRuntimeState: runtime.resetRuntimeState },
    meta: { appendSelectedTaskLog: runtime.appendSelectedTaskLog },
  }), [runtime]);
  return <ScholarRuntimeProvider value={context}>
    <div className="scholar-workbench">
      <WorkbenchHeader sessionsOpen={sessionsOpen} onToggleSessions={() => setSessionsOpen(!sessionsOpen)} onOpenResearch={() => setResearchOpen(true)} />
      {researchOpen && <ResearchWorkspace identity={chatFlow.requestIdentity} attachments={chatFlow.pendingAttachments}
        onReadPaper={attachment => pdfFlow.setPdfUrl(`${API_BASE_URL}${attachment.content_url}`)}
        onPlanCreated={chatFlow.acceptResearchPlan} onClose={() => setResearchOpen(false)} />}
      <ScholarWorkspace nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        logsEndRef={logsEndRef} layout={layout} chatFlow={chatFlow} pdfFlow={pdfFlow} mobilePane={mobilePane}
        onMobilePaneChange={setMobilePane} sessionsOpen={sessionsOpen} onCloseSessions={() => setSessionsOpen(false)} />
    </div>
  </ScholarRuntimeProvider>;
}
