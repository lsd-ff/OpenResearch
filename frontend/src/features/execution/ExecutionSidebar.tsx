import type { RefObject } from 'react';
import { CheckCircle2, Code, Eye, GitBranch, LoaderCircle, Maximize2, Minimize2, Play, TerminalSquare, X } from 'lucide-react';
import type { ExecutionDisplayMode } from '../../app/hooks/useScholarRuntime';
import type { Task } from '../../contracts/api';
import { agentNames, statusNames } from '../shared/taskPresentation';
import { getAgentIcon } from '../shared/agentVisuals';
import { ExecutionContent, type CompactMode } from './ExecutionContent';
import { OutputFiles } from './OutputFiles';

interface Props {
  selectedTask: Task; width: string; isExecuting: boolean; displayMode: ExecutionDisplayMode;
  executionLogs: string; executionResult: string; executionCode: string; executionStructuredData: string;
  executionImage: string; logsEndRef: RefObject<HTMLDivElement | null>;
  onClose: () => void; onExecute: () => void; onChangeDisplayMode: (mode: ExecutionDisplayMode) => void;
}

function Budget({ task }: { task: Task }) {
  if (task.Type !== 'ablation_design' || !task.Inputs) return null;
  const limits = [['实验', 'ablation_max_experiments', '组'], ['GPU', 'ablation_max_gpu_minutes', '分钟'], ['总耗时', 'ablation_max_wall_minutes', '分钟']];
  return <div className="execution-budget">{limits.map(([label, key, unit]) => {
    const value = task.Inputs?.[key];
    return <div key={key}><small>{label}</small><strong>{typeof value === 'number' || typeof value === 'string' ? value : '—'} {unit}</strong></div>;
  })}</div>;
}

function taskTabs(props: Props): Array<{ mode: CompactMode; label: string; icon: typeof Eye }> {
  return [
    { mode: 'logs' as const, label: '实时日志', icon: TerminalSquare },
    ...(props.executionResult ? [{ mode: 'report' as const, label: '分析报告', icon: Eye }] : []),
    ...(props.executionCode ? [{ mode: 'code' as const, label: '沙箱代码', icon: Code }] : []),
    ...(props.executionImage ? [{ mode: 'plot' as const, label: '生成图表', icon: Maximize2 }] : []),
    ...(['claim_evidence_build', 'research_execute'].includes(props.selectedTask.Type || '') && props.executionStructuredData
      ? [{ mode: 'evidence' as const, label: '证据图', icon: GitBranch }] : []),
  ];
}

export function ExecutionSidebar(props: Props) {
  const { selectedTask: task, displayMode, onChangeDisplayMode } = props;
  const expanded = displayMode.endsWith('-expanded');
  const mode = displayMode.replace('-expanded', '') as CompactMode;
  const completed = task.Status === 'completed';
  const running = task.Status === 'in_progress';
  const canRun = !props.isExecuting && ['pending', 'ready'].includes(task.Status || '');
  return <aside className={`scholar-execution-sidebar ${expanded ? 'is-expanded' : ''}`} style={{ width: props.width }} aria-label="任务详情">
    <div className="inspector-heading"><h2>{expanded ? task.Name : '任务详情'}</h2>
      <button className="icon-button" onClick={expanded ? () => onChangeDisplayMode(mode) : props.onClose}
        title={expanded ? `退出全屏${mode === 'report' ? '报告' : mode === 'plot' ? '图表' : '证据图'}` : '关闭节点执行面板'}
        aria-label={expanded ? '退出全屏查看' : '关闭节点执行面板'}>{expanded ? <Minimize2 size={18} /> : <X size={18} />}</button>
    </div>
    {!expanded && <>
      <div className="inspector-task">
        <div className="inspector-task-title"><span className="task-agent-icon">{getAgentIcon(task.AssignedTo)}</span><h3>{task.Name}</h3></div>
        <div className="inspector-task-meta"><span className="agent-tag" title={task.AssignedTo}>{agentNames[task.AssignedTo] || task.AssignedTo}</span>
          <span className="task-status" data-status={task.Status}>{running ? <LoaderCircle size={14} className="animate-spin" /> : completed ? <CheckCircle2 size={14} /> : null}{statusNames[task.Status] || task.Status}</span></div>
        <Budget task={task} />
        {!completed && <button className="execute-task-button" onClick={props.onExecute} disabled={!canRun}>
          {running ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />}{running ? '正在执行…' : '启动 Agent 任务'}
        </button>}
      </div>
      <div className="inspector-tabs" aria-label="任务输出视图">
        <div>{taskTabs(props).map(tab => <button key={tab.mode} aria-pressed={mode === tab.mode}
          className={mode === tab.mode ? 'is-active' : ''} onClick={() => onChangeDisplayMode(tab.mode)}><tab.icon size={14} />{tab.label}</button>)}</div>
        {['report', 'plot', 'evidence'].includes(mode) && <button className="icon-button" onClick={() => onChangeDisplayMode(`${mode}-expanded` as ExecutionDisplayMode)}
          title={mode === 'report' ? '全屏阅读报告' : mode === 'plot' ? '全屏查看图表' : '全屏查看证据图'}><Maximize2 size={15} /></button>}
      </div>
    </>}
    <div className="inspector-content">
      <ExecutionContent mode={mode} logs={props.executionLogs} result={props.executionResult} code={props.executionCode}
        structuredData={props.executionStructuredData} image={props.executionImage} logsEndRef={props.logsEndRef} expanded={expanded} />
      <OutputFiles raw={props.executionStructuredData} image={props.executionImage} />
    </div>
  </aside>;
}
