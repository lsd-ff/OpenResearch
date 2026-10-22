import type { RefObject } from 'react';
import { TerminalSquare } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { ClaimEvidenceGraphView } from '../claim-evidence/ClaimEvidenceGraphView';
import { imageSource } from './imageSource';

export type CompactMode = 'logs' | 'report' | 'code' | 'plot' | 'evidence';
export interface ExecutionContentProps {
  mode: CompactMode; logs: string; result: string; code: string;
  structuredData: string; image: string; logsEndRef: RefObject<HTMLDivElement | null>;
  expanded?: boolean;
}

export function ExecutionContent({ mode, logs, result, code, structuredData, image, logsEndRef, expanded }: ExecutionContentProps) {
  if (mode === 'logs') return <div className="execution-log-view">
    <div className="execution-content-caption"><TerminalSquare size={13} />执行输出<span>日志自动保存</span></div>
    <div className="execution-terminal" role="log" aria-label="任务实时日志">
      {logs || '暂无日志；任务记录同步后将在这里显示。'}<div ref={logsEndRef} />
    </div>
  </div>;
  if (mode === 'plot') return <div className="execution-plot"><img src={imageSource(image)} alt={expanded ? 'Full Resolution Plot' : 'Generated Plot'} /></div>;
  if (mode === 'evidence') return <div className="execution-evidence"><ClaimEvidenceGraphView rawGraph={structuredData} expanded={expanded} /></div>;
  return <div className={`execution-prose ${mode === 'code' ? 'is-code' : ''}`}>
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
      {mode === 'code' ? `\`\`\`python\n${code}\n\`\`\`` : result}
    </ReactMarkdown>
  </div>;
}
