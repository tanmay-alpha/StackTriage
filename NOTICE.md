# Notice and attribution

StackTriage is copyright (c) 2026 Tanmay Mangal and released under the MIT License (see `LICENSE`).

## Relationship to AIML_LogAnalyser

StackTriage was started after reviewing
[swagatblog/AIML_LogAnalyser](https://github.com/swagatblog/AIML_LogAnalyser)
(commit `fa072e2`, MIT licensed). The review is written up in
`docs/foundation-audit.md`.

**As of this release, no source file from AIML_LogAnalyser is copied into this
repository.** Its design ideas informed StackTriage (an sklearn
preprocessing + Isolation Forest wrapper with a single fit/score/persist
interface), but every module here was written from scratch for a different
input type (application log text and time-window features rather than
fixed-column device telemetry). `scripts/audit_upstream.py` only *runs*
upstream code from a separate checkout to reproduce the audit findings; it does
not vendor it.

If upstream code is adapted in a later commit, that file will be listed here
and will keep upstream's copyright and permission notice.

The upstream license, reproduced so the terms stay attached to the ideas and any
future adapted code:

```
MIT License

Copyright (c) 2026 ML Log Anomaly Detector contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

(Individual upstream files also carry an author header naming Swagat Parida.)

## Test data

All files in `tests/fixtures/` are synthetic, produced by
`scripts/make_fixtures.py` with a fixed seed. They contain no real user data;
the "secrets" in them (passwords, tokens, emails) are made-up strings used to
test redaction.

## Runtime dependencies

The core package has no third-party runtime dependencies. Dependencies added in
later milestones (scikit-learn, FastAPI, SQLAlchemy, React, ...) are listed with
their licenses in the README once introduced.
