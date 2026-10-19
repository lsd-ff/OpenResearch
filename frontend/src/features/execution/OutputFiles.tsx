import { API_BASE_URL } from '../../config/env';
import { imageFilename, imageSource } from './imageSource';

interface FileLink { name: string; size: number; url: string }

function fileLinks(raw: string): FileLink[] {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || !('output_files' in value) || !Array.isArray(value.output_files)) return [];
    return value.output_files.filter((item): item is FileLink =>
      Boolean(item) && typeof item === 'object' && typeof item.name === 'string' &&
      typeof item.size === 'number' && typeof item.url === 'string' &&
      /^\/api\/plans\/[a-f0-9]{32}\/artifacts\/[a-f0-9]{32}\/files\/[0-7]$/.test(item.url));
  } catch { return []; }
}

export function OutputFiles({ raw, image }: { raw: string; image: string }) {
  const files = fileLinks(raw);
  const preview = imageSource(image);
  if (!files.length && !preview) return null;
  return <section aria-label="产物文件" className="mt-3 rounded-xl border border-slate-200 bg-white p-3 text-xs">
    <p className="mb-2 font-semibold text-slate-700">产物文件</p>
    <div className="flex flex-wrap gap-3">
      {files.map(file => <a key={file.url} className="text-blue-700 underline" href={`${API_BASE_URL}${file.url}`} download>
        下载 {file.name}（{file.size} 字节）
      </a>)}
      {preview && <a className="text-blue-700 underline" href={preview} download={imageFilename(preview)}>下载图表</a>}
    </div>
  </section>;
}
