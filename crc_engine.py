"""
crc_engine.py — Reverse-engineered ECC implementation for Revit's CRCFile payload format.

Revit encodes certain OLE streams with a proprietary LFSR-based error correction scheme.
This module implements the encoding side: profile selection, layout calculation, and parity
emission. It is used by patcher.py to regenerate a valid ECC tail after modifying stream content.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import List, Sequence, Tuple


MASK56 = 0x00FFFFFFFFFFFFFF
MASK64 = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class CrcProfile:
    checksum_bits: int
    lfsr_poly: int
    max_row_bits: int
    slack_field_bits: int
    col_align: int
    variant: int


@dataclass(frozen=True)
class CrcLayout:
    rows: int
    cols: int


def _ceil_log2_u32(x: int) -> int:
    n = 0
    v = 1
    while v < x:
        v <<= 1
        n += 1
    return n


def _derive_variant(checksum_bits: int, poly: int) -> int:
    top = 1 << (checksum_bits - 1)
    rem = poly & ~top
    if (poly & top) == 0:
        return 0
    if rem == 0 or (rem & (rem - 1)) != 0:
        return 0
    if checksum_bits == 2:
        return 0
    pos = 0
    while pos < checksum_bits - 2:
        pos += 1
        if rem & 1:
            return pos
        rem >>= 1
    return 0


def make_profile(checksum_bits: int, poly: int, max_row_bits: int, col_align: int) -> CrcProfile:
    min_bits = max_row_bits * col_align
    if min_bits < 65:
        min_bits = 65
    slack_field_bits = _ceil_log2_u32((min_bits + 7) >> 3)
    variant = _derive_variant(checksum_bits, poly)
    return CrcProfile(
        checksum_bits=checksum_bits,
        lfsr_poly=poly,
        max_row_bits=max_row_bits,
        slack_field_bits=slack_field_bits,
        col_align=col_align,
        variant=variant,
    )


PROFILES: Tuple[CrcProfile, ...] = (
    make_profile(11, 0x500, 0x7FF, 2),
    make_profile(9, 0x110, 0x1FF, 2),
    make_profile(7, 0x060, 0x07F, 2),
    make_profile(6, 0x030, 0x03F, 2),
    make_profile(5, 0x014, 0x01F, 2),
    make_profile(4, 0x00C, 0x00F, 2),
    make_profile(3, 0x005, 0x007, 2),
    make_profile(2, 0x003, 0x003, 4),
)


def calc_sizes(profile: CrcProfile, n_bytes: int, from_encoded_size: bool) -> CrcLayout:
    bits = n_bytes * 8
    if not from_encoded_size:
        bits += profile.slack_field_bits
        span = profile.max_row_bits - profile.checksum_bits
        if bits <= span * 65:
            return CrcLayout(rows=((bits + 64) // 65) + profile.checksum_bits, cols=65)
        cols = ((bits - 1) + span) // span
        if cols < 66:
            raise ValueError("invalid CRC layout: cols < 66")
        rem = (cols - 65) % profile.col_align
        if rem:
            cols += profile.col_align - rem
        return CrcLayout(rows=profile.max_row_bits, cols=cols)
    cols = bits // profile.max_row_bits
    if cols > 65:
        cols -= (cols - 65) % profile.col_align
    if cols < 65:
        return CrcLayout(rows=bits // 65, cols=65)
    return CrcLayout(rows=profile.max_row_bits, cols=cols)


def pre_checksum_bits(profile: CrcProfile, encoded_bytes: int) -> int:
    layout = calc_sizes(profile, encoded_bytes, True)
    return (layout.rows - profile.checksum_bits) * layout.cols


def encoded_bytes_for_payload(profile: CrcProfile, payload_bytes: int) -> int:
    layout = calc_sizes(profile, payload_bytes, False)
    return ((layout.rows * layout.cols) + 7) >> 3


def payload_bytes_from_encoded(profile: CrcProfile, encoded_bytes: int) -> int:
    pre_bits = pre_checksum_bits(profile, encoded_bytes)
    delta = 0
    if profile.slack_field_bits < pre_bits:
        delta = pre_bits - profile.slack_field_bits
    return delta >> 3


def compute_threshold_tables(profiles: Sequence[CrcProfile]) -> Tuple[List[int], List[int]]:
    lo: List[int] = []
    hi: List[int] = []
    for i in range(len(profiles) - 1):
        cur = profiles[i]
        nxt = profiles[i + 1]
        ratio = cur.checksum_bits / cur.max_row_bits
        seed = ceil(nxt.checksum_bits / ratio)
        approx = ((seed * 65) + 7) >> 3
        lower_payload = payload_bytes_from_encoded(nxt, approx - 1)
        lower_encoded = encoded_bytes_for_payload(cur, lower_payload)
        upper_payload = payload_bytes_from_encoded(cur, lower_encoded)
        lo.append(lower_encoded)
        hi.append(upper_payload)
    return lo, hi


LOW_THRESHOLDS, HIGH_THRESHOLDS = compute_threshold_tables(PROFILES)

assert HIGH_THRESHOLDS == [13364, 3046, 795, 357, 153, 56, 15]
assert LOW_THRESHOLDS == [13455, 3120, 854, 407, 195, 90, 41]


def select_profile(payload_bytes: int) -> CrcProfile:
    idx = 7
    for i in range(6, -1, -1):
        if payload_bytes <= HIGH_THRESHOLDS[i]:
            break
        idx -= 1
    return PROFILES[idx]


def bit_extract_le(buf: bytes | bytearray, bit_off: int, bit_len: int) -> int:
    if bit_len == 0:
        return 0
    out = 0
    for i in range(bit_len):
        byte_index = (bit_off + i) >> 3
        bit_index = (bit_off + i) & 7
        out |= ((buf[byte_index] >> bit_index) & 1) << i
    return out


def bit_insert_le(buf: bytearray, bit_off: int, bit_len: int, value: int) -> None:
    for i in range(bit_len):
        byte_index = (bit_off + i) >> 3
        bit_index = (bit_off + i) & 7
        bit = (value >> i) & 1
        if bit:
            buf[byte_index] |= 1 << bit_index
        else:
            buf[byte_index] &= ~(1 << bit_index) & 0xFF


def lfsr_update(
    profile: CrcProfile,
    buf: bytes | bytearray,
    encoded_bytes: int,
    rem_from_high: bool = True,
) -> List[int]:
    cols = calc_sizes(profile, encoded_bytes, True).cols
    pre_bits = pre_checksum_bits(profile, encoded_bytes)
    state_rows = [0] * cols
    lane = cols - 1

    full_bytes = pre_bits >> 3
    for i in range(full_bytes):
        b = buf[i]
        for _ in range(8):
            lane = 0 if (lane + 1 == cols) else (lane + 1)
            reg = state_rows[lane]
            nxt = reg >> 1
            if ((reg ^ b) & 1) != 0:
                nxt ^= profile.lfsr_poly
            state_rows[lane] = nxt & 0xFFFFFFFF
            b >>= 1

    rem = pre_bits & 7
    if rem:
        b = buf[full_bytes]
        if rem_from_high:
            b >>= 8 - rem
        for _ in range(rem):
            lane = 0 if (lane + 1 == cols) else (lane + 1)
            reg = state_rows[lane]
            nxt = reg >> 1
            if ((reg ^ b) & 1) != 0:
                nxt ^= profile.lfsr_poly
            state_rows[lane] = nxt & 0xFFFFFFFF
            b >>= 1

    return state_rows


def emit_parity_simple(
    profile: CrcProfile,
    buf: bytearray,
    encoded_bytes: int,
    rem_from_high: bool = True,
) -> None:
    state_rows = lfsr_update(profile, buf, encoded_bytes, rem_from_high=rem_from_high)
    cols = len(state_rows)
    pre_bits_val = pre_checksum_bits(profile, encoded_bytes)
    for lane in range(cols):
        reg = state_rows[lane]
        out_bit = pre_bits_val + lane
        for _ in range(profile.checksum_bits):
            if reg & 1:
                buf[out_bit >> 3] ^= 1 << (out_bit & 7)
            reg >>= 1
            out_bit += cols
