# Baseline (v4.1) — restoration status

## Summary

The `FileScope.py` that was committed to `main` is a self-extracting wrapper: a
gzip+base64 payload of the original v4.1 source plus
`exec(compile(gzip.decompress(base64.b64decode(_PAYLOAD))...))`.

The payload **cannot be fully decoded**. It is corrupt from roughly the middle
of the base64 payload onward, so only the first part of the v4.1 source could be
recovered. Exact baseline restoration is BLOCKED until the original raw
`FileScope.py` v4.1 is supplied.

## Evidence

Reproduce with the vendored, execute-free decoder:

```powershell
python scripts/restore_payload.py baseline/FileScope_v4_1_wrapper.py.txt baseline/out.py
# -> zlib.error: Error -3 while decompressing data: invalid distance too far back
```

Measured facts:

* The committed blob equals GitHub's raw file (only CRLF differs):
  `git` blob `d4b73108a3ff242bc68f0d7dd1fede99f1a1a688`,
  local SHA-256 `ED93B799...`, raw.githubusercontent SHA-256 `3C8F6609...`,
  `local.replace(b"\r\n", b"\n") == remote` → `True`.
* `_PAYLOAD` is one implicitly concatenated string constant of **18,913**
  characters — not a multiple of 4, so it is not valid base64 as committed.
* Base64-decoded size 14,184 bytes; gzip header is valid
  (`1f 8b 08 00`, XFL `02`).
* Prefixes of the payload decode cleanly up to **9,308** characters
  (6,981 compressed bytes → 22,757 bytes of source). Every prefix of 9,312
  characters or more fails. Corruption therefore starts at payload character
  ~9,308.
* The first 22,749 of those bytes are valid UTF-8 Python; the final 8 bytes are
  misaligned fragments (for example `with urn PS   for c_l-onlxcepDATA`), i.e.
  the tail of the "clean" region is already damaged.
* Repair attempts that failed:
  * delete any single payload character (18,913 candidates) — no hit;
  * substitute any base64 character in the window 9,290–9,340 — no hit;
  * look for a duplicated block (>60 chars) anywhere in the payload — none;
  * resume the deflate stream at every later offset and alignment — only
    meaningless fragments are produced, never a complete gzip member.

Conclusion: a contiguous block of the compressed payload is missing or
replaced, and the missing bytes cannot be inferred. Expect roughly half of the
original file to be unrecoverable (the recovered prefix is 641 lines).

## What is kept here

* `FileScope_v4_1_wrapper.py.txt` — the committed v4.1 wrapper, verbatim, as
  evidence. It is not imported or executed by anything.
* `FileScope_v4_1_recovered_prefix.py` — the recovered 641-line prefix
  (22,757 bytes, with one replacement character at the truncation point). It is
  intentionally incomplete; it does not run and is used only as a behavioural
  reference for V5.

## Consequence for V5

Behaviour that must not regress is defined by the V5 specification plus the
recovered prefix, which pins down these v4.1 semantics:

* `SearchOptions` fields, `DEFAULT_WORKERS = 4`, `MAX_IN_MEMORY_HITS = 250_000`,
  `MAX_REMOTE_STAGE_BYTES = 256 MiB`, `TEMP_FREE_RESERVE_BYTES = 4 GiB`,
  `UNKNOWN_TEXT_MAX_BYTES = 50 MiB`, `TEXT_PROBE_BYTES = 64 KiB`.
* `SearchMatcher`: NFKC width folding, casefold, part-number canonicalisation
  (hyphen variants and whitespace dropped, `*` wildcard), `N of (...)` file
  level threshold, `&` / `,` / `;` grouping, Japanese `かつ` / `または`
  operators, and file-level AND (`unit_terms` + `file_displays`).
* Settings path `%LOCALAPPDATA%\FileScope\settings.json` with the legacy
  `search_tool_settings.json` fallback.
* `is_remote_path` (UNC or `DRIVE_REMOTE`), Tesseract discovery list and
  `jpn+eng` detection, remote staging rules, Excel candidate expansion for
  ints/floats/bools/dates, unknown-text probe.

To restore the exact baseline (and to re-verify v4.1 behaviour line by line),
the raw v4.1 `FileScope.py` is needed.
