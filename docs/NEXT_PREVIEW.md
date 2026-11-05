# Next preview: responsiveness and results

Unreleased work based on v5.1.1. Index candidate generation runs on the
discovery thread; event draining has a 12 ms per-pass budget, excluding redraws.
The result window retains its scroll offset. Overlapping roots are deduplicated
by normalized absolute path, without resolving symlinks or network aliases.
CSV has a separate display facet; TSV remains Text and extraction is unchanged.

On generated local text data, one baseline/candidate run before the later
deduplication and CSV changes found 10,000-file direct search times of 3.48/3.42 s,
40 ms timer gap p95 of 64/45 ms, and maximum gap of 116/123 ms. For 2,000
warm-indexed files, start callback times were 32.8/2.9 ms and search times
0.509/0.502 s. These single hidden-window measurements do not establish
interactive usability or represent the final candidate's performance.

Outstanding: native interactive UI, SMB, memory and CPU comparison, cancellation
latency under load, process isolation experiment, completion and preview stalls,
column settings, match explanations, and issue-list operations. Candidate is not
a release. Existing settings and index formats are unchanged.
