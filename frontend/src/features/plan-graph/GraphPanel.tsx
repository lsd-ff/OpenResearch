import { Background, Controls, ReactFlow } from '@xyflow/react';
import type { Edge, Node, OnEdgesChange, OnNodesChange, ReactFlowInstance } from '@xyflow/react';
import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, GitBranch, LoaderCircle, Maximize2, Minimize2, Play, RotateCcw, ShieldCheck, Square } from 'lucide-react';
import type { IntentContext } from '../../contracts/api';
import { ResearchArtifacts } from './ResearchArtifacts';

interface GraphPanelProps {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: OnNodesChange<Node>;
  onEdgesChange: OnEdgesChange<Edge>;
  onNodeClick: (_: unknown, node: Node) => void;
  intentContext: IntentContext | null;
  runAllText: string;
  graphTitle: string;
  graphHint: string;
  isExecuting: boolean;
	requiresApproval: boolean;
  onRunAll: () => void;
	onApproveAndRun: () => void;
	onCancel: () => void;
	onRetryFailed: () => void;
  expanded: boolean;
  onToggleExpanded: () => void;
}

export function GraphPanel(props: GraphPanelProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const graphKey = props.nodes.map(node => node.id).join(':');
  useEffect(() => {
    const element = viewport.current;
    if (!element || !flow) return;
    let frame = 0;
    const observer = new ResizeObserver(entries => {
      if (!entries[0]?.contentRect.width) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        void flow.fitView({ padding: 0.16, maxZoom: 1.08 });
      });
    });
    observer.observe(element);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [flow, graphKey]);
  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onNodeClick,
    intentContext,
    runAllText,
    graphTitle,
    graphHint,
    isExecuting,
	requiresApproval,
    onRunAll,
	onApproveAndRun,
	onCancel,
	onRetryFailed,
  } = props;
  const completedCount = nodes.filter((node) => node.data.status === 'completed').length;
  const runningCount = nodes.filter((node) => node.data.status === 'in_progress').length;
	const failedCount = nodes.filter((node) => node.data.status === 'failed').length;
  const pendingCount = nodes.filter((node) => node.data.task && node.data.status !== 'completed').length;
  const subtitle = intentContext?.raw_intent || graphHint;

  return (
    <section className="graph-panel" aria-label="执行流程">
      <div className="graph-panel-header">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800" title={subtitle}>
              <GitBranch className="h-4 w-4 text-blue-600" />
              {graphTitle}
            </h2>
            <div className="graph-progress flex flex-wrap items-center gap-2 text-xs">
              <span className="flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-1 text-emerald-700">
                <CheckCircle2 className="h-3.5 w-3.5" />
                {completedCount}/{nodes.length} 完成
              </span>
              {runningCount > 0 && (
                <span className="flex items-center gap-1 rounded-md bg-amber-50 px-2 py-1 text-amber-700">
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  {runningCount} 运行中
                </span>
              )}
			  {failedCount > 0 && <span className="rounded-md bg-rose-50 px-2 py-1 font-medium text-rose-700">{failedCount} 失败</span>}
            </div>
          </div>
		  <div className="flex shrink-0 items-center gap-2">
            <button className="icon-button" onClick={props.onToggleExpanded}
              title={props.expanded ? '收起执行流程' : '展开执行流程'} aria-label={props.expanded ? '收起执行流程' : '展开执行流程'}>
              {props.expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            </button>
			{isExecuting ? (
			  <button
				type="button"
				onClick={onCancel}
				title="取消计划"
				className="flex items-center gap-2 rounded-md border border-rose-200 bg-white px-3.5 py-2 text-sm font-semibold text-rose-700 transition-colors hover:bg-rose-50"
			  >
				<Square className="h-3.5 w-3.5 fill-current" />
				取消
			  </button>
			) : requiresApproval ? (
			  <button
				type="button"
				onClick={onApproveAndRun}
				className="flex items-center gap-2 rounded-md bg-emerald-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700"
			  >
				<ShieldCheck className="h-4 w-4" />
				审批并运行
			  </button>
			) : failedCount > 0 ? (
			  <button
				type="button"
				onClick={onRetryFailed}
				className="flex items-center gap-2 rounded-md bg-amber-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-amber-700"
			  >
				<RotateCcw className="h-4 w-4" />
				重试失败节点
			  </button>
			) : (
			  <button
				type="button"
				onClick={onRunAll}
				disabled={pendingCount === 0}
				className="flex items-center gap-2 rounded-md bg-blue-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
			  >
				<Play className="h-4 w-4 fill-current" />
				{runAllText}
			  </button>
			)}
		  </div>
        </div>
      </div>

      <div ref={viewport} className="graph-viewport">
        <ReactFlow
          onInit={setFlow}
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          nodesConnectable={false}
          nodesDraggable={false}
          minZoom={0.1}
          fitView
          fitViewOptions={{ padding: 0.16, maxZoom: 1.08 }}
          className="research-flow-canvas"
        >
          <Background color="#dce5e9" gap={24} size={0.7} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <ResearchArtifacts nodes={nodes} />
    </section>
  );
}
