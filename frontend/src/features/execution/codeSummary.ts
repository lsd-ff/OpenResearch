import type { GraphTask, PlanGraph } from '../../contracts/api';

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function recoveryCounts(raw?: string): { attempts: number; repairs: number; python?: string } | undefined {
  if (!raw) return;
  try {
    const report: unknown = JSON.parse(raw);
    if (!report || typeof report !== 'object' || !('version' in report) ||
      report.version !== 'code.recovery/v1' || !('attempts' in report) ||
      !Array.isArray(report.attempts) || !('decisions' in report) ||
      !Array.isArray(report.decisions)) return;
    let repairs = 0;
    for (const decision of report.decisions) {
      if (!record(decision)) continue;
      if (decision.accepted === true) repairs += 1;
      const preparation = decision.preparation;
      if (!record(preparation) || !Array.isArray(preparation.decisions)) continue;
      repairs += preparation.decisions.filter(change => record(change) &&
        change.accepted === true && record(change.runtime_after)).length;
    }
    const last: unknown = report.attempts.at(-1);
    const environment = record(last) ? last.environment : undefined;
    const python = record(environment) && typeof environment.python === 'string' ? environment.python : undefined;
    return { attempts: report.attempts.length, repairs, python };
  } catch { return; }
}

export function codeSummary(graph: Pick<PlanGraph, 'nodes'>,
  task: Pick<GraphTask, 'status' | 'structured_data' | 'result'>): string {
  const execution = recoveryCounts(task.structured_data);
  if (!execution) return task.result ?? '';
  const prepare = recoveryCounts(graph.nodes.find(node => node.type === 'code_generate')?.structured_data);
  const repairs = execution.repairs + (prepare?.repairs ?? 0);
  const state = task.status === 'completed' ? '程序执行完成' : task.status === 'failed' ? '程序执行失败' : '程序状态已更新';
  const version = execution.python ? `\n实际 Python：${execution.python}` : '';
  return `${state}；运行/安装尝试 ${execution.attempts} 次，采用 ${repairs} 次修复。${version}\n\n${task.result ?? ''}`;
}
