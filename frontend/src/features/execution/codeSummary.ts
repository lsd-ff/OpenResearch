import type { GraphTask, PlanGraph } from '../../contracts/api';

function recoveryCounts(raw?: string): { attempts: number; repairs: number } | undefined {
  if (!raw) return;
  try {
    const report: unknown = JSON.parse(raw);
    if (!report || typeof report !== 'object' || !('version' in report) ||
      report.version !== 'code.recovery/v1' || !('attempts' in report) ||
      !Array.isArray(report.attempts) || !('decisions' in report) ||
      !Array.isArray(report.decisions)) return;
    return { attempts: report.attempts.length, repairs: report.decisions.filter(
      item => item && typeof item === 'object' && item.accepted === true).length };
  } catch { return; }
}

export function codeSummary(graph: PlanGraph, task: GraphTask): string {
  const execution = recoveryCounts(task.structured_data);
  if (!execution) return task.result ?? '';
  const prepare = recoveryCounts(graph.nodes.find(node => node.type === 'code_generate')?.structured_data);
  const repairs = execution.repairs + (prepare?.repairs ?? 0);
  return `程序执行完成；运行/安装尝试 ${execution.attempts} 次，采用 ${repairs} 次修复。\n\n${task.result ?? ''}`;
}
