import { useState, type CSSProperties, type RefObject } from 'react';
import type { Edge, Node, OnEdgesChange, OnNodesChange } from '@xyflow/react';
import { BookOpen, GitBranch, MessageSquareText, X } from 'lucide-react';
import { useScholarRuntimeContext } from '../context/ScholarRuntimeContext';
import type { useScholarChatFlow } from '../hooks/useScholarChatFlow';
import type { usePdfAssistFlow } from '../hooks/usePdfAssistFlow';
import type { useScholarLayoutState } from '../hooks/useScholarLayoutState';
import { useGraphExecutionViewModel } from '../viewModels/useGraphExecutionViewModel';
import { ChatSessionManager } from '../../features/chat/ChatSessionManager';
import { ChatPanel } from '../../features/chat/ChatPanel';
import { PdfPanel } from '../../features/pdf-viewer/PdfPanel';
import { GraphPanel } from '../../features/plan-graph/GraphPanel';
import { ExecutionSidebar } from '../../features/execution/ExecutionSidebar';
import { API_BASE_URL } from '../../config/env';
import { WorkspaceOverview } from './WorkspaceOverview';
import { ColumnResizeHandle } from './ColumnResizeHandle';

interface Props {
  nodes: Node[]; edges: Edge[]; onNodesChange: OnNodesChange<Node>; onEdgesChange: OnEdgesChange<Edge>;
  logsEndRef: RefObject<HTMLDivElement | null>; layout: ReturnType<typeof useScholarLayoutState>;
  chatFlow: ReturnType<typeof useScholarChatFlow>; pdfFlow: ReturnType<typeof usePdfAssistFlow>;
  mobilePane: 'chat' | 'graph'; onMobilePaneChange: (pane: 'chat' | 'graph') => void;
  sessionsOpen: boolean; onCloseSessions: () => void;
}

export function ScholarWorkspace(props: Props) {
  const { chatFlow, pdfFlow, layout, mobilePane, onMobilePaneChange } = props;
  const runtime = useScholarRuntimeContext();
  const [expandedGraph, setExpandedGraph] = useState(false);
  const viewModel = useGraphExecutionViewModel({ ...props, intentContext: chatFlow.intentContext,
    activePlanId: chatFlow.activePlanId, activePlanStatus: chatFlow.activePlanStatus });
  const session = chatFlow.sessionSummaries.find(item => item.id === chatFlow.activeSessionId);
  const closePdf = () => pdfFlow.setPdfUrl(null);
  const chatActions = {
    setPrompt: chatFlow.setPrompt, setShowSuggestions: pdfFlow.setShowSuggestions,
    onSendMessage: chatFlow.handleSendMessage, setLoginInput: chatFlow.setLoginInput,
    onLogin: chatFlow.handleLogin,
    onCreateSession: () => { closePdf(); setExpandedGraph(false); chatFlow.handleCreateSession(); },
    // 导航始终可用；切换研究时收起上一会话的文档和展开视图，避免串用阅读上下文。
    onSwitchSession: (sessionId: string) => { closePdf(); setExpandedGraph(false); chatFlow.handleSwitchSession(sessionId); },
    onAttachFiles: chatFlow.handleAttachFiles,
    onRemoveAttachment: chatFlow.handleRemoveAttachment,
  };
  return <div className="scholar-app-shell" data-mobile-pane={pdfFlow.pdfUrl ? 'chat' : mobilePane} data-has-inspector={Boolean(viewModel.executionSidebarProps)}>
    <ChatSessionManager open={props.sessionsOpen} onClose={props.onCloseSessions}
      state={{ ...chatFlow, sessions: chatFlow.sessionSummaries, loading: chatFlow.loading }} actions={chatActions} />
    <div className="scholar-graph-workspace" style={layout.workflowWidth === null ? undefined : { '--workflow-preferred-width': `${layout.workflowWidth}px` } as CSSProperties}>
      <main className="scholar-left-workspace" aria-label="研究工作区">
        <WorkspaceOverview title={chatFlow.intentContext?.raw_intent || session?.title || '开始新的研究'} intentType={chatFlow.intentContext?.intent_type}
          status={chatFlow.activePlanStatus} nodes={props.nodes} onOpenTask={runtime.actions.handleOpenTaskView} />
        <div className="scholar-mobile-tabbar">
          <button onClick={() => onMobilePaneChange('chat')} className={mobilePane === 'chat' ? 'is-active' : ''}><MessageSquareText size={16} />对话</button>
          <button onClick={() => { closePdf(); onMobilePaneChange('graph'); }} className={mobilePane === 'graph' ? 'is-active' : ''}><GitBranch size={16} />流程 {props.nodes.length || ''}</button>
        </div>
        {pdfFlow.pdfUrl ? <section className="document-workspace" aria-label="论文阅读器">
          <div className="document-heading"><span><BookOpen size={17} />论文阅读</span><button className="secondary-button" onClick={closePdf} aria-label="关闭阅读器"><X size={16} />返回对话</button></div>
          <PdfPanel {...pdfFlow} pdfUrl={pdfFlow.pdfUrl} onFullTranslation={pdfFlow.handleFullTranslation} />
        </section> : <ChatPanel
          state={{ ...chatFlow, showSuggestions: pdfFlow.showSuggestions, sessions: chatFlow.sessionSummaries }}
          chatActions={chatActions} pdfActions={{ onReadPaper: attachment => pdfFlow.setPdfUrl(`${API_BASE_URL}${attachment.content_url}`), onClosePdf: closePdf, onFullTranslation: pdfFlow.handleFullTranslation }}
          taskActions={{ onOpenTaskView: runtime.actions.handleOpenTaskView }} />}
      </main>
      {!expandedGraph && <ColumnResizeHandle label="调整执行流程宽度" targetId="workflow-pane" minimum={224}
        onResize={layout.resizeWorkflow} onReset={layout.resetWorkflow} />}
      <aside id="workflow-pane" className={`workspace-flow ${expandedGraph ? 'is-expanded' : ''}`} aria-label="执行流程栏">
        <GraphPanel {...viewModel.graphPanelProps} expanded={expandedGraph} onToggleExpanded={() => setExpandedGraph(!expandedGraph)} />
      </aside>
      {viewModel.showExecutionResizeHandle && <ColumnResizeHandle label="调整任务详情宽度" targetId="task-details-pane" minimum={304}
        onResize={layout.resizeSidebar} onReset={layout.resetSidebar} />}
      {viewModel.executionSidebarProps && <ExecutionSidebar {...viewModel.executionSidebarProps} />}
    </div>
  </div>;
}
