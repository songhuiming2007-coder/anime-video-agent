// JSONL 分行器（Spec 8 §2.4）：按 \n 切行，最后一段无换行的留作 partial——半行永不解析、永不下发。
// partial 以字节保存，避免把多字节 UTF-8 字符从中间切开。
const NL = 0x0a;
const decoder = new TextDecoder("utf-8");

export class LineSplitter {
  private partial: Uint8Array = new Uint8Array(0);
  /** partial 溢出后丢弃直到下一个换行（该换行之后才是完整行） */
  private discarding = false;

  constructor(private readonly maxPartialBytes: number) {}

  push(chunk: Uint8Array): { lines: string[]; overflow: boolean } {
    const lines: string[] = [];
    let overflow = false;
    let start = 0;
    for (let i = 0; i < chunk.length; i++) {
      if (chunk[i] !== NL) continue;
      if (this.discarding) {
        this.discarding = false;
      } else {
        const piece = chunk.subarray(start, i);
        const whole = this.partial.length ? concat(this.partial, piece) : piece;
        lines.push(decoder.decode(whole));
      }
      this.partial = new Uint8Array(0);
      start = i + 1;
    }
    if (start < chunk.length && !this.discarding) {
      const rest = chunk.subarray(start);
      const next = this.partial.length ? concat(this.partial, rest) : rest.slice();
      if (next.length > this.maxPartialBytes) {
        overflow = true;
        this.partial = new Uint8Array(0);
        this.discarding = true;
      } else {
        this.partial = next;
      }
    }
    return { lines, overflow };
  }

  reset(): void {
    this.partial = new Uint8Array(0);
    this.discarding = false;
  }

  /** 从下一个换行之后开始接收（snapshot 只载尾部时跳过被截断的首行） */
  skipToNextLine(): void {
    this.partial = new Uint8Array(0);
    this.discarding = true;
  }

  pendingBytes(): number {
    return this.partial.length;
  }
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}
