# RVT History Patcher

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-blue.svg)]()
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)]()

> Rewrite the save-history username inside a Revit `.rvt` file — cleanly, in seconds, without breaking the file's internal integrity checks.

Useful for **anonymizing project files before sharing them publicly**, **scrubbing former employees out of long-running project history**, or any case where you need a Revit file's recorded author names to read something other than what's currently there.

---

## Screenshot

> _Add a screenshot of the GUI here once you take one — drop a PNG into the repo and reference it as `![GUI](docs/gui.png)`._

---

## Why this exists

Open a `.rvt` file in a hex editor and you'll see usernames embedded throughout the save-history table — every "Save" event Revit has ever recorded for that file is logged with the OS username of whoever pressed the button. There's no built-in way to clear or edit it.

That becomes a problem when you want to:

- Share a sample file publicly without leaking real names
- Hand a file off to a client or contractor while keeping internal team names private
- Comply with a data-removal request when an employee leaves
- Strip identifying metadata before publishing template/seed files

Hex-editing the names directly **doesn't work** — Revit protects the history stream with a proprietary LFSR-based error correction code (ECC). Modify a single byte and the file becomes unloadable. This tool reverse-engineers that ECC, regenerates a valid checksum tail for the new content, and writes the result back into the OLE container so Revit opens the file as if nothing happened.

---

## Features

- **GUI** — point, click, patch. No command line required.
- **CLI** — scriptable for batch jobs and CI pipelines.
- **Auto-detection** — reads the file and tells you which usernames are in it before you patch.
- **Auto-ECC recovery** — figures out the file's specific ECC parameters by itself; works on every Revit version we've tested.
- **Size-exact gzip repacking** — preserves stream length so the OLE container layout doesn't shift.
- **In-place or copy** — patch the original or write to a new file, your choice.

---

## Install

Requires **Windows** (the OLE write-back uses `pywin32`) and **Python 3.10+**.

```bash
git clone https://github.com/TyroneJaime/revit-history-patcher.git
cd revit-history-patcher
pip install -r requirements.txt
```

---

## Usage

### GUI

```bash
python gui.py
```

1. Click **Browse** and pick your `.rvt` file
2. Click **Detect username** — the tool lists every author name found and how many save entries each one has
3. Type a **new username** (must be ≤ current length — see caveats below)
4. Choose in-place or save-as
5. Click **Patch**

Live progress, including ECC detection and gzip size matching, prints to the log panel.

### CLI

```bash
# See who's in the file
python cli.py detect project.rvt

# Patch in-place (auto-detects current username)
python cli.py patch project.rvt --new "new.user5678"

# Patch to a new file with explicit old name
python cli.py patch project.rvt --old "old.user1234" --new "new.user5678" -o patched.rvt
```

### Python API

```python
from patcher import detect_usernames, patch_rvt

# See who's in the file
names = detect_usernames("project.rvt")
# [("old.user1234", 42), ...]

# Patch it
patch_rvt(
    rvt_path="project.rvt",
    old_user="old.user1234",
    new_user="new.user5678",
    output_path="project_patched.rvt",   # omit to patch in-place
)
```

---

## Building a standalone `.exe`

If you want to ship the GUI to non-Python users, PyInstaller produces a single executable:

```bash
pip install pyinstaller
pyinstaller revit-history-patcher.spec
```

The output `dist/revit-history-patcher.exe` is fully self-contained — no Python install required on the target machine.

---

## Limits and caveats

Worth knowing before you ship a patched file:

### Length cap: new name ≤ current name in the file

The new username's character count must be **less than or equal to** whatever username is currently in the file. The patcher must repack the gzip-compressed history stream to exactly the original byte size (the OLE container layout depends on it), and once the new plaintext exceeds the original's natural compressed minimum, gzip can't shrink to fit. Going shorter is fine — the tool pads with random bytes until the size matches.

This is a one-way ratchet: each patch tightens the cap. If you go from 18 chars → 15 chars, the next patch is capped at 15. You can't grow back up.

### Each patch targets one author at a time

If the file has saves by multiple users, `patch` replaces *one* name across all its entries. Other users in the history stay. Run the patcher once per author you want to scrub.

### Only `Global/DocumentIncrementTable` is touched

This is the stream that records every Save event with the OS username — the main place identity leaks in normal `.rvt` files. Other streams that *can* contain user info (`BasicFileInfo`, `Worksharing/...` for central files, etc.) are not modified. For typical single-user files this is enough; for workshared central files, more streams may need handling.

Project Information fields like Owner / Building Name etc. are filled in manually by users, not auto-stamped, so they're not a concern unless the original author typed them in.

### Re-saving in Revit re-pollutes

The moment you open a patched file in Revit and hit Save, Revit appends a new entry with **your current OS username**. To stay clean, transfer the patched file without re-saving it, or change your OS username before saving.

### ASCII detection only

`detect_usernames` lists names with printable ASCII characters (32–126). Names with accents or non-Latin characters won't appear in the auto-detect list, but `patch` will still work if you type the exact string yourself.

### Revit version sensitivity

Tested against recent single-user `.rvt` files. If the brute-force ECC parameter detection can't find a match, the tool errors out cleanly without writing — your file is safe. If you hit this, file an issue with the Revit version that produced the file.

---

## How the ECC works

Revit chooses one of eight CRC profiles based on payload size, each defined by:

| Field | Description |
|---|---|
| `checksum_bits` | Parity bits per LFSR lane (2–11) |
| `lfsr_poly` | Feedback polynomial |
| `max_row_bits` | Maximum row width |
| `slack_field_bits` | Bits used to encode padding length |
| `col_align` | Column alignment constraint |

The payload is laid out as a 2D grid of rows × columns. An LFSR is run over each column of the pre-checksum region, producing one register per lane. The register bits are then interleaved back into the parity region of the output buffer.

The `slack` field encodes how many padding bits sit between the end of the payload and the start of the checksum region. Revit's encoder places this field at a fixed negative offset from `pre_checksum_bits` with a value that doesn't follow the naive formula. Rather than hard-coding the offset, this tool brute-forces it against the original (known-good) ECC tail in under a second — meaning the patcher works without prior knowledge of the file's specific encoding configuration.

---

## Files

| File | Purpose |
|---|---|
| `crc_engine.py` | Reverse-engineered ECC implementation (pure stdlib) |
| `patcher.py` | Core patching API: stream I/O, gzip handling, ECC recovery and regeneration |
| `gui.py` | Tkinter GUI front-end |
| `cli.py` | Command-line front-end |
| `revit-history-patcher.spec` | PyInstaller config for building a standalone `.exe` |
| `requirements.txt` | Python dependencies |

---

## Disclaimer

This tool modifies file metadata only — it does not alter geometry, families, views, or any other project content. Always keep a backup of the original file before patching. Use it on files you own or have permission to modify. Don't use it to misrepresent authorship of work you did not produce.

---

## License

MIT — see [LICENSE](LICENSE).
