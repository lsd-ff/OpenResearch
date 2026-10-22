export interface FileLink { name: string; size: number; url: string }

export function fileLinks(raw: string): FileLink[] {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || !('output_files' in value) || !Array.isArray(value.output_files)) return [];
    return value.output_files.filter((item): item is FileLink =>
      Boolean(item) && typeof item === 'object' && typeof item.name === 'string' &&
      typeof item.size === 'number' && typeof item.url === 'string' &&
      /^\/api\/plans\/[a-f0-9]{32}\/artifacts\/[a-f0-9]{32}\/files\/[0-7]$/.test(item.url));
  } catch { return []; }
}
