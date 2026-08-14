"""RLE compression for the EPD image transfer protocol.

Byte-for-byte mirror of EPD-nRF5/html/js/rle.js:
  - repeat run : control = 0x80 | (len - 3), len in [3..130]
  - literal run: control = len - 1, len in [1..128]
  - `rle_chunks` splits the compressed stream only at code boundaries so
    every chunk is a self-contained RLE stream (matches rleCompressMTU).
"""

from __future__ import annotations


def rle_compress(data: bytes, max_literal: int = 128) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        run_len = 1
        while i + run_len < n and run_len < 130 and data[i + run_len] == data[i]:
            run_len += 1

        if run_len >= 3:
            out.append(0x80 | (run_len - 3))
            out.append(data[i])
            i += run_len
        else:
            start = i
            lit_len = 0
            while i < n and lit_len < max_literal:
                if i + 2 < n and data[i] == data[i + 1] and data[i] == data[i + 2]:
                    break
                lit_len += 1
                i += 1
            if lit_len == 0:
                out.append(0x00)
                out.append(data[i])
                i += 1
            else:
                out.append(lit_len - 1)
                out.extend(data[start:start + lit_len])
    return bytes(out)


def rle_chunks(data: bytes, chunk_size: int) -> list[bytes]:
    """Compress then split at code boundaries; each chunk <= chunk_size."""
    max_lit = min(chunk_size - 1, 128)
    comp = rle_compress(data, max_lit)
    chunks: list[bytes] = []
    i = 0
    start = 0
    n = len(comp)
    while i < n:
        control = comp[i]
        code_len = 2 if (control & 0x80) else (control + 2)
        if (i - start) + code_len > chunk_size and i > start:
            chunks.append(comp[start:i])
            start = i
        i += code_len
    if i > start:
        chunks.append(comp[start:i])
    return chunks


def raw_chunks(data: bytes, chunk_size: int) -> list[bytes]:
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]


def _rle_decompressed(src: bytes, out_len: int) -> bytes:
    """Reference decoder used by self-tests (mirrors EPD_service.c)."""
    dst = bytearray()
    pos = 0
    while pos < len(src) and len(dst) < out_len:
        control = src[pos]
        if control & 0x80:
            count = (control & 0x7F) + 3
            if pos + 1 >= len(src):
                break
            pos += 1
            value = src[pos]
            pos += 1
            for _ in range(min(count, out_len - len(dst))):
                dst.append(value)
        else:
            count = control + 1
            if pos + 1 + count > len(src):
                break
            pos += 1
            for _ in range(count):
                dst.append(src[pos])
                pos += 1
    return bytes(dst)