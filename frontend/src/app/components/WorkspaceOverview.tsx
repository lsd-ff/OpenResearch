import { CheckCircle2, CircleAlert, Clock3, FileText, LoaderCircle } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { Task } from '../../contracts/api';
import { intentNames, statusNames } from '../../features/shared/taskPresentation';

interface Props {
  title: string; intentType?: string; status: string | null; nodes: Node[];
  onOpenTask: (taskId: string, mode: 'report' | 'plot') => void;
}

export function WorkspaceOverview({ title, intentType, status, nodes, onOpenTask }: Props) {
  const completed = nodes.filter(node => node.data.status === 'completed').length;
  const reportNode = [...nodes].reverse().find(node => (node.data.task as Task | undefined)?.Result);
  const running = status === 'in_progress' || status === 'ready';
  const StatusIcon = running ? LoaderCircle : status === 'completed' ? CheckCircle2 : status === 'failed' ? CircleAlert : Clock3;
  return <div className="workspace-overview">
    <div className="workspace-breadcrumb">研究会话<span>/</span><span className="workspace-intent">{intentNames[intentType || ''] || '科研助手'}</span></div>
    <div className="workspace-title-row"><h1 title={title}>{title}</h1>
      {reportNode && <button className="secondary-button" onClick={() => onOpenTask(reportNode.id, 'report')}><FileText size={16} />查看报告</button>}
    </div>
    <div className="workspace-summary" data-status={status || 'idle'}>
      {status ? <><span><StatusIcon size={16} className={running ? 'animate-spin' : ''} />{statusNames[status] || status}</span>
        <i /><span><span>{nodes.length} 个步骤</span> · <span>{completed} 个已完成</span></span></>
        : <span>从一个问题出发，连接论文、代码与研究结果。</span>}
    </div>
  </div>;
}
