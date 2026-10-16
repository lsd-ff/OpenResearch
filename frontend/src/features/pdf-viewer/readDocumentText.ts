import type { PdfJs } from '@react-pdf-viewer/core';

export async function readDocumentText(document: PdfJs.PdfDocument): Promise<string> {
  if (document.numPages > 100) throw new Error('文档超过 100 页，请使用划词翻译。');
  const pages: string[] = [];
  let total = 0;
  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber++) {
    const page = await document.getPage(pageNumber);
    const content = await page.getTextContent();
    const text = content.items.map(item => 'str' in item ? item.str : '').join(' ');
    total += text.length;
    if (total > 12000) throw new Error('全文超过本次翻译字数上限，请使用划词翻译。');
    pages.push(`第 ${pageNumber} 页：\n${text || '本页未提取到文字，需要 OCR。'}`);
  }
  return pages.join('\n\n');
}
