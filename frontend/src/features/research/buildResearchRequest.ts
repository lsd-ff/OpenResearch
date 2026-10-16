import type { UploadedFile } from '../../contracts/api';
import type { ResearchRequest } from '../../contracts/research';

export interface ResearchForm {
  intent: string; url: string; ref: string; entrypoint: string;
  dependencies: string; code: string; dataset: UploadedFile | null;
  columns: string; target: string; identifier: string;
  task: 'classification' | 'regression'; threshold: string; ablation: string;
  attachments: UploadedFile[];
}

export function buildResearchRequest(form: ResearchForm): ResearchRequest {
  const metric = form.task === 'classification' ? 'accuracy' : 'mse';
  const expected = Number(form.threshold);
  if (form.threshold && !Number.isFinite(expected)) throw new Error('阈值必须是有限数值。');
  if (form.threshold && !form.dataset) throw new Error('设置指标阈值前请上传评估数据。');
  const format = form.dataset?.name.endsWith('.jsonl') ? 'jsonl' : form.dataset?.name.endsWith('.tsv') ? 'tsv' : 'csv';
  return {
    intent: form.intent, workflow: form.dataset ? 'benchmark' : 'reproduce', code: form.code.trim() || undefined,
    attachments: form.attachments.filter(item => item.name.toLowerCase().endsWith('.pdf')).map(item => item.id),
    research: {
      repository: form.url ? { url: form.url, ref: form.ref } : undefined,
      environment: { requirements: form.dependencies.split(/\n/).map(value => value.trim()).filter(Boolean) },
      entrypoint: form.entrypoint, max_repairs: 1,
      benchmark: form.dataset ? {
        upload_id: form.dataset.id, format, input_columns: form.columns.split(',').map(value => value.trim()).filter(Boolean),
        target_column: form.target, id_column: form.identifier || undefined, task: form.task, metrics: [metric],
      } : undefined,
      ablations: form.ablation ? [{ name: 'ablation', arguments: form.ablation.split(/\s+/).filter(Boolean) }] : [],
      rubric: { claims: form.dataset && form.threshold ? [{
        title: `${metric} 验收`, statement: `声明数据集上的 ${metric} 达到阈值 ${expected}`,
        criteria: [{ description: '由完整预测重算指标', metric_name: metric, expected_value: expected,
          operator: form.task === 'classification' ? 'at_least' : 'at_most', tolerance: 0 }],
      }] : [] },
    },
  };
}
