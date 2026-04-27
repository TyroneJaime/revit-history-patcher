"""
patcher.py — Core RVT history stream patching API.

Provides detect_usernames() and patch_rvt() for reading and rewriting the
Global/DocumentIncrementTable stream inside a Revit .rvt OLE file.
"""

from __future__ import annotations

import gzip
import random
import shutil
import struct
import zlib
from pathlib import Path
from typing import Callable, Optional

import olefile
import pythoncom

import crc_engine as crc


# ---------------------------------------------------------------------------
# Stream reading helpers
# ---------------------------------------------------------------------------

def _find_gzip_boundary(data: bytes) -> int:
    obj = zlib.decompressobj(47)
    obj.decompress(data)
    return len(data) - len(obj.unused_data)


def _read_stream(rvt_path: str) -> bytes:
    ole = olefile.OleFileIO(rvt_path)
    raw = ole.openstream(["Global", "DocumentIncrementTable"]).read()
    ole.close()
    return raw


def detect_usernames(rvt_path: str) -> list[tuple[str, int]]:
    """Return [(username, count), ...] sorted by frequency, descending."""
    raw = _read_stream(rvt_path)
    gzip_end = _find_gzip_boundary(raw[8:])
    decompressed = zlib.decompress(raw[8 : 8 + gzip_end], 47)

    counts: dict[str, int] = {}
    i = 0
    while i < len(decompressed) - 4:
        length = struct.unpack_from("<I", decompressed, i)[0]
        if 1 <= length <= 200:
            start = i + 4
            end = start + length * 2
            if end + 2 <= len(decompressed):
                try:
                    name = decompressed[start:end].decode("utf-16-le")
                    if all(32 <= ord(c) < 127 for c in name):
                        counts[name] = counts.get(name, 0) + 1
                except UnicodeDecodeError:
                    pass
        i += 1

    return sorted(counts.items(), key=lambda x: -x[1])


# ---------------------------------------------------------------------------
# ECC helpers
# ---------------------------------------------------------------------------

def _detect_ecc_params(
    prefix: bytes,
    ecc_actual: bytes,
    prof: crc.CrcProfile,
    encoded_bytes: int,
    pre_bits_val: int,
) -> tuple[int, int, bool]:
    """
    Recover the slack field offset, value, and rem_from_high flag by testing
    all combinations against the known-good ECC tail from the original stream.
    Raises ValueError if no match is found.
    """
    for pos_offset in range(-64, 9):
        for slack_val in range(0, 128):
            for rfh in (True, False):
                out = bytearray(encoded_bytes)
                out[: len(prefix)] = prefix
                crc.bit_insert_le(out, pre_bits_val + pos_offset, prof.slack_field_bits, slack_val)
                crc.emit_parity_simple(prof, out, encoded_bytes, rem_from_high=rfh)
                if bytes(out[len(prefix) :]) == ecc_actual:
                    return pos_offset, slack_val, rfh
    raise ValueError(
        "Could not determine ECC parameters from the existing stream. "
        "The file may use an unsupported encoding variant."
    )


def _generate_ecc(
    prefix: bytes,
    prof: crc.CrcProfile,
    encoded_bytes: int,
    pre_bits_val: int,
    pos_offset: int,
    slack_val: int,
    rfh: bool,
) -> bytes:
    out = bytearray(encoded_bytes)
    out[: len(prefix)] = prefix
    crc.bit_insert_le(out, pre_bits_val + pos_offset, prof.slack_field_bits, slack_val)
    crc.emit_parity_simple(prof, out, encoded_bytes, rem_from_high=rfh)
    return bytes(out[len(prefix) :])


# ---------------------------------------------------------------------------
# Gzip helpers
# ---------------------------------------------------------------------------

def _revit_gzip_compress(data: bytes) -> bytes:
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    ba = bytearray(compressed)
    ba[9] = 0x0B  # Revit sets OS byte to 0x0B (NTFS)
    return bytes(ba)


def _force_gzip_size(plaintext: bytes, target_len: int, seed: int = 42) -> bytes:
    """
    Append random printable bytes to plaintext until the gzip output is exactly
    target_len bytes. Returns the compressed result.
    Raises RuntimeError if a match cannot be found within MAX_ATTEMPTS.
    """
    MAX_ATTEMPTS = 50_000
    rng = random.Random(seed)
    current = plaintext
    attempts = 0

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        cg = _revit_gzip_compress(current)

        if len(cg) == target_len:
            return cg

        if len(cg) > target_len:
            current = current[:-1]
            cg = _revit_gzip_compress(current)
            if len(cg) == target_len:
                return cg
            current = plaintext + bytes(rng.choices(range(32, 127), k=attempts // 10 + 1))
        else:
            current += bytes([rng.randint(32, 127)])

    raise RuntimeError(
        f"Could not force gzip output to {target_len} bytes after {MAX_ATTEMPTS} attempts."
    )


# ---------------------------------------------------------------------------
# OLE write-back
# ---------------------------------------------------------------------------

def _write_stream(rvt_path: str, stream_path: list[str], data: bytes) -> None:
    mode = 0x00000002 | 0x00000010 | 0x00000000  # READWRITE | SHARE_EXCLUSIVE | DIRECT
    root = pythoncom.StgOpenStorage(str(rvt_path), None, mode)
    opened = [root]
    current = root
    try:
        for sub in stream_path[:-1]:
            stor = current.OpenStorage(sub, None, mode, None, 0)
            opened.append(stor)
            current = stor
        stream = current.OpenStream(stream_path[-1], None, mode, 0)
        stream.Seek(0, 0)
        stream.SetSize(len(data))
        stream.Seek(0, 0)
        stream.Write(data)
        stream.Commit(0)
        for stor in reversed(opened):
            stor.Commit(0)
    finally:
        del stream
        for stor in reversed(opened):
            del stor


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch_rvt(
    rvt_path: str,
    old_user: str,
    new_user: str,
    output_path: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Replace every history entry of old_user with new_user in an RVT file.

    Parameters
    ----------
    rvt_path    : path to the source .rvt file
    old_user    : username to replace (must match exactly what's in the file)
    new_user    : replacement username
    output_path : destination path; if None or same as rvt_path, patches in-place
    log         : optional callback(str) for progress messages

    Returns
    -------
    dict with keys: replaced (int), stream_bytes (int)

    Raises
    ------
    ValueError   if ECC params cannot be determined from the original stream
    RuntimeError if gzip size matching fails
    """
    if log is None:
        log = print

    out_path = str(output_path) if output_path else str(rvt_path)
    if out_path != str(rvt_path):
        shutil.copy2(rvt_path, out_path)
        log(f"Copied to {out_path}")

    log("Reading stream...")
    raw = _read_stream(out_path if out_path != str(rvt_path) else rvt_path)

    revit_header = raw[:8]
    after_header = raw[8:]
    gzip_end = _find_gzip_boundary(after_header)
    target_gzip_len = gzip_end
    prefix_orig = raw[: 8 + gzip_end]
    ecc_actual = raw[8 + gzip_end :]

    log(f"Stream: {len(raw)} bytes  |  gzip: {gzip_end} bytes  |  ECC: {len(ecc_actual)} bytes")

    # Decompress and patch
    decompressed = zlib.decompress(after_header[:gzip_end], 47)
    old_utf = old_user.encode("utf-16-le")
    new_utf = new_user.encode("utf-16-le")
    old_block = struct.pack("<I", len(old_user)) + old_utf + b"\x00\x00"
    new_block = struct.pack("<I", len(new_user)) + new_utf + b"\x00\x00"
    count = decompressed.count(old_block)
    patched_dec = decompressed.replace(old_block, new_block)
    log(f"Replaced {count} occurrence(s) of '{old_user}' → '{new_user}'")

    if count == 0:
        log("WARNING: username not found in stream — output will be unchanged")

    # Re-compress to exact original gzip size
    log(f"Forcing gzip output to {target_gzip_len} bytes...")
    new_gzip = _force_gzip_size(patched_dec, target_gzip_len)
    log(f"Gzip target matched.")

    new_prefix = revit_header + new_gzip

    # Determine ECC parameters from the original stream
    prof = crc.select_profile(len(prefix_orig))
    encoded_bytes = crc.encoded_bytes_for_payload(prof, len(prefix_orig))
    pre_bits_val = crc.pre_checksum_bits(prof, encoded_bytes)

    log("Detecting ECC parameters...")
    pos_offset, slack_val, rfh = _detect_ecc_params(
        prefix_orig, ecc_actual, prof, encoded_bytes, pre_bits_val
    )
    log(f"ECC params: pos_offset={pos_offset}, slack={slack_val}, rem_from_high={rfh}")

    # Regenerate ECC for the new prefix
    log("Generating new ECC tail...")
    new_ecc = _generate_ecc(new_prefix, prof, encoded_bytes, pre_bits_val, pos_offset, slack_val, rfh)

    final_stream = new_prefix + new_ecc
    log(f"Final stream: {len(final_stream)} bytes")

    log("Writing to file...")
    _write_stream(out_path, ["Global", "DocumentIncrementTable"], final_stream)
    log("Done.")

    return {"replaced": count, "stream_bytes": len(final_stream)}
