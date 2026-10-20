import { inflateRawSync } from "node:zlib";

/** Read ZIP local entries produced by the server (seekable buffer, deflate). */
export function flightPackageFiles(bytes: Buffer): Map<string, Buffer> {
  const entries = new Map<string, Buffer>();
  let offset = 0;
  while (offset + 30 <= bytes.length && bytes.readUInt32LE(offset) === 0x04034b50) {
    const method = bytes.readUInt16LE(offset + 8);
    const size = bytes.readUInt32LE(offset + 18);
    const nameLength = bytes.readUInt16LE(offset + 26);
    const extraLength = bytes.readUInt16LE(offset + 28);
    const name = bytes.subarray(offset + 30, offset + 30 + nameLength).toString();
    const start = offset + 30 + nameLength + extraLength;
    const compressed = bytes.subarray(start, start + size);
    if (method !== 8 && method !== 0) throw new Error(`Unsupported ZIP method ${method}`);
    entries.set(name, method === 8 ? inflateRawSync(compressed) : compressed);
    offset = start + size;
  }
  if (!entries.has("manifest.json")) throw new Error("Flight package manifest is missing");
  return entries;
}
