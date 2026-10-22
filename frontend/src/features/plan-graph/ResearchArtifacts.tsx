import type { Node } from '@xyflow/react';
import { Code2, Download, FileText } from 'lucide-react';
import type { Task } from '../../contracts/api';
import { fileLinks } from '../execution/outputFileLinks';
import { API_BASE_URL } from '../../config/env';

interface Artifact { key: string; name: string; kind: string; url?: string; text?: string }

function artifactsFromNodes(nodes: Node[]): Artifact[] {
  const tasks = nodes.map(node => node.data.task as Task).filter(Boolean);
  const files: Artifact[] = tasks.flatMap(task => fileLinks(task.StructuredData || '').map(file => ({
    key: file.url, name: file.name, kind: '执行产物', url: `${API_BASE_URL}${file.url}`,
  })));
  const code = [...tasks].reverse().find(task => task.Code);
  if (code?.Code) files.push({ key: `${code.ID}:code`, name: 'experiment.py', kind: '实际执行代码', text: code.Code });
  const report = [...tasks].reverse().find(task => task.Result && task.Status === 'completed');
  if (report?.Result) files.push({ key: `${report.ID}:report`, name: '执行报告.md', kind: '节点报告', text: report.Result });
  return files.filter((file, index) => files.findIndex(candidate => candidate.key === file.key) === index);
}

function downloadText(artifact: Artifact) {
  const url = URL.createObjectURL(new Blob([artifact.text || ''], { type: 'text/plain;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = artifact.name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ArtifactLink({ artifact }: { artifact: Artifact }) {
  const content = <>{artifact.name.endsWith('.py') ? <Code2 size={22} /> : <FileText size={22} />}
    <span><strong>{artifact.name}</strong><small>{artifact.kind}</small></span><Download size={16} /></>;
  return artifact.url ? <a className="artifact-link" href={artifact.url} download>{content}</a>
    : <button className="artifact-link" onClick={() => downloadText(artifact)}>{content}</button>;
}

export function ResearchArtifacts({ nodes }: { nodes: Node[] }) {
  const artifacts = artifactsFromNodes(nodes);
  if (!artifacts.length) return null;
  return <section className="research-artifacts" aria-label="研究产物"><h3>研究产物<span>{artifacts.length} 个文件</span></h3>
    <div className="artifact-list">{artifacts.slice(0, 3).map(artifact => <ArtifactLink key={artifact.key} artifact={artifact} />)}</div>
    {artifacts.length > 3 && <details><summary>查看其余 {artifacts.length - 3} 个产物</summary>
      <div className="artifact-list">{artifacts.slice(3).map(artifact => <ArtifactLink key={artifact.key} artifact={artifact} />)}</div></details>}
  </section>;
}
