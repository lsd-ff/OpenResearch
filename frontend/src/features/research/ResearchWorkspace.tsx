import { useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { PlanResponse, UploadedFile } from '../../contracts/api';
import { buildResearchRequest } from './buildResearchRequest';
import { createResearchPlan, uploadFile } from '../../services/api/scholarApi';
import type { RequestIdentity } from '../../services/api/scholarApi';

interface Props {
  identity: RequestIdentity;
  attachments: UploadedFile[];
  onPlanCreated: (response: PlanResponse) => void;
  onClose: () => void;
  onReadPaper: (attachment: UploadedFile) => void;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="research-field"><span>{label}</span>{children}</label>;
}

function errorMessage(error: unknown): string {
  const response = error as { response?: { data?: { error?: string; detail?: unknown } } };
  const data = response.response?.data;
  return data?.error || (data?.detail ? JSON.stringify(data.detail) : '请求失败，请检查 API 连接和输入。');
}

export function ResearchWorkspace({ identity, attachments, onPlanCreated, onClose, onReadPaper }: Props) {
  const [intent, setIntent] = useState('');
  const [url, setUrl] = useState('');
  const [ref, setRef] = useState('HEAD');
  const [entrypoint, setEntrypoint] = useState('openresearch_run.py');
  const [dependencies, setDependencies] = useState('');
  const [code, setCode] = useState('');
  const [dataset, setDataset] = useState<UploadedFile | null>(null);
  const [columns, setColumns] = useState('');
  const [target, setTarget] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [task, setTask] = useState<'classification' | 'regression'>('classification');
  const [threshold, setThreshold] = useState('');
  const [ablation, setAblation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const metric = task === 'classification' ? 'accuracy' : 'mse';

  async function uploadDataset(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError('');
    try { setDataset(await uploadFile(file, identity)); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const request = buildResearchRequest({ intent, url, ref, entrypoint, dependencies, code, dataset,
        columns, target, identifier, task, threshold, ablation, attachments });
      onPlanCreated(await createResearchPlan(request, identity)); onClose();
    }
    catch (failure) { setError(failure instanceof Error && !('response' in failure) ? failure.message : errorMessage(failure)); }
    finally { setBusy(false); }
  }

  return <div className="research-overlay" onKeyDown={event => { if (event.key === 'Escape' && !busy) onClose(); }}>
    <section className="research-panel" role="dialog" aria-modal="true" aria-labelledby="research-title">
    <header><div><span className="research-caption">OpenResearch · 可追溯实验</span><h2 id="research-title">配置复现实验</h2></div>
      <button type="button" onClick={onClose} aria-label="关闭实验配置">关闭</button></header>
    <form onSubmit={submit}>
      <Field label="实验目标"><textarea autoFocus required value={intent} onChange={event => setIntent(event.target.value)} placeholder="说明要运行的实验与期望比较" /></Field>
      {attachments.filter(item => item.name.toLowerCase().endsWith('.pdf')).map(item =>
        <button key={item.id} type="button" onClick={() => { onReadPaper(item); onClose(); }}>阅读 {item.name}</button>)}
      <fieldset><legend>源码与环境</legend><div className="research-columns">
        <Field label="GitHub 仓库"><input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://github.com/owner/repo" /></Field>
        <Field label="提交或分支"><input value={ref} required onChange={event => setRef(event.target.value)} /></Field>
        <Field label="Python 入口"><input value={entrypoint} required onChange={event => setEntrypoint(event.target.value)} /></Field>
        <Field label="固定依赖（每行 name==version）"><textarea value={dependencies} onChange={event => setDependencies(event.target.value)} placeholder="包含所需的间接依赖" /></Field>
      </div><Field label="入口代码（可选）"><textarea className="research-code" value={code} onChange={event => setCode(event.target.value)} placeholder="已有入口可留空；新入口需要提供代码或配置模型" /></Field></fieldset>
      <fieldset><legend>数据与预测</legend>
        <Field label="评估数据 CSV / TSV / JSONL"><input type="file" accept=".csv,.tsv,.jsonl" disabled={busy} onChange={event => void uploadDataset(event.target.files?.[0])} /></Field>
        {dataset && <p className="research-source">{dataset.name} · SHA-256 {dataset.sha256.slice(0, 16)}…</p>}
        <div className="research-columns"><Field label="输入列（逗号分隔）"><input value={columns} required={Boolean(dataset)} onChange={event => setColumns(event.target.value)} /></Field>
          <Field label="目标列"><input value={target} required={Boolean(dataset)} onChange={event => setTarget(event.target.value)} /></Field>
          <Field label="样本 ID 列（可选）"><input value={identifier} onChange={event => setIdentifier(event.target.value)} /></Field>
          <Field label="评估任务"><select value={task} onChange={event => setTask(event.target.value as typeof task)}><option value="classification">分类</option><option value="regression">回归</option></select></Field></div>
        <p className="research-help">预测入口从 OPENRESEARCH_INPUT 读取输入，向 OPENRESEARCH_PREDICTIONS 写入包含 id、prediction 的 JSON 数组。目标列保留在评估端。</p>
      </fieldset>
      <fieldset><legend>验收与对照</legend><div className="research-columns">
        <Field label={`${metric} ${task === 'classification' ? '最低值' : '最高值'}（可选）`}><input type="number" step="any" value={threshold} onChange={event => setThreshold(event.target.value)} /></Field>
        <Field label="消融运行参数（可选）"><input value={ablation} onChange={event => setAblation(event.target.value)} placeholder="--disable-feature" /></Field></div>
        <p className="research-help">标准在运行前冻结。创建后在流程图中执行，并查看真实输出和主张证据。</p>
      </fieldset>
      {error && <p role="alert" className="research-error">{error}</p>}
      <footer><span>{attachments.length} 个已选论文附件</span><button type="submit" disabled={busy}>{busy ? '处理中…' : '生成实验计划'}</button></footer>
    </form>
  </section></div>;
}
