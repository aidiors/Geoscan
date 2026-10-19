

export function download(content: string | Blob, fileName: string, mimeType: string): void {
  const url = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}
