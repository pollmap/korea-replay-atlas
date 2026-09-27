# Private apartment collection on GitHub Actions

`property-collect.yml` executes the real collector on a standard public-repository
Ubuntu runner. It is distinct from the older plan-only workflow. It does not
publish website data: the successfully audited **private D1 archive head** advances.
Public data still requires normalization, release audit, candidate upload and app
release pinning. Do not report that the public map auto-updates from this workflow.

The active workflow explicitly uses `--scope priority-nine --require-scope-complete`:
Gyeonggi → Seoul → Incheon → Cheonan → Asan → Sejong → Cheongju → Daejeon → Busan.
Its exact allowlist has 112 codes from the checked-in official registry; it does
not match place-name substrings or silently admit new codes. Missing target codes
stop before source collection. `expected_region_count`, `region_count` and
`missing_codes` are reported. Small isolated tests may omit the completeness flag.
The CLI's default/explicit `nationwide` remains backward compatible, but the
scheduled collector and default plan workflow now select the nine-area scope.

## Activation and limits

Configure these repository secrets without logging their values:

| Secret | Purpose |
| --- | --- |
| `DATA_GO_KR_SERVICE_KEY` | Approved MOLIT apartment sale/rent API service key |
| `CLOUDFLARE_API_TOKEN` | Token restricted to the existing private archive D1 account/databases |
| `PROPERTY_ARCHIVE_CONFIG` | Existing archive config: `account_id`, `control_database`, four `object_databases` |

The restricted archive broker is an alternative to the Cloudflare management
token. When selected, set repository secrets `PROPERTY_ARCHIVE_BROKER_TOKEN` and
`PROPERTY_ARCHIVE_BROKER_URL` together. The worker endpoint is pinned by
the transport and serves only the fixed archive operations. Partial broker
configuration fails closed; it never silently falls back to a management token.
The broker secret is distinct from the source API key and deployment OAuth.
See `PROPERTY_ARCHIVE_BROKER.md` for deployment and verification requirements.

Missing credentials stop before any remote or source request. No OAuth session,
browser state, raw response, checkpoint or secret file is uploaded to GitHub.

Dispatch a manual run and verify its result, then set repository variable
`PROPERTY_COLLECTION_ENABLED=true` to activate the schedule. Setting it to `false`
stops future scheduled runs; deliberate manual dispatch remains available for
diagnosis. Main branch only, standard `ubuntu-24.04`, read-only GitHub permission,
120-minute emergency timeout, no paid runner/cache/artifact service. Schedule is UTC
00:17/04:17/08:17/12:17/16:17/20:17 (KST 09:17/13:17/17:17/21:17/01:17/05:17); GitHub may delay schedules.
The workflow must be enabled and present on the default branch.

The default matches the verified operating budget: **100 source requests and
64 MiB source response bytes**. Repository variables `PROPERTY_COLLECTION_MAX_REQUESTS`
(1–500) and `PROPERTY_COLLECTION_MAX_BYTES` (8–64 MiB, in bytes) can lower or
adjust the budget without code changes. Measure actual archive writes/time before
any further increase. All bounds are validated before remote reads. Extending the
history window does not raise these budgets or promise a 20-year completion date.
The private archive has additional hydration, verification and upload traffic.
Existing provider-per-day accounting and D1 free storage/write guards remain in
force across all runners. A manual trigger does not bypass those guards.

Before any source reservation, the leased run checks `raw_budget_preflight` on
every object shard. The bound uses the configured maximum response bytes,
zlib's worst-case expansion, base64/chunk rounding and the existing per-row
storage allowance; it assumes all hashes could land on one shard. It does not
extrapolate previously observed bytes per job. If any shard lacks this raw budget,
the report is `storage_paused`, with zero requests, unchanged head, normally
released lease and no invented retry time. The command exits successfully for
this deliberate pause, but it is not a collected/published result. A new run can
resume after capacity or the configured budget changes. No data is deleted.

This check covers raw payload allowance only: it does **not** reserve space for
future normalized snapshots, checkpoint/manifest growth, exact SQLite page
allocation, other writers or shared daily quotas. Those still use the existing
put-time checks and fail-closed recovery. Unknown shard sizes stop instead of
assuming empty storage. See `PROPERTY_STORAGE_AUDIT_20260927.md` for the measured
storage composition and remaining archival work.

## Work progression

The latest verified private head is restored selectively into a new UUID directory
on every run. Existing parent objects remain immutable. The 241-month plan expands
when a month changes, preserving every previously planned historical month and
original source response. Region ordering remains the existing user's priority.

All source selection paths use the same scope: new acquisition, recent/history
refresh reset, safe-error requeue, stale-pagination preflight and month-completion
cursor checks. Outside jobs and their originals/last snapshots are preserved and
never requested by this workflow. The whole ledger still expands rectangularly
for checkpoint/migration compatibility; no raw/snapshot file is created merely
for an unselected placeholder. `coverage`/`acquisition` remain whole-ledger
reports; `scope_coverage`/`scope_acquisition` report the actual selected work.

Three successful runs in four backfill unfinished jobs. Every fourth run revisits
one of the latest three contract months (minimum seven-day age); every 28th run
revisits an older month (minimum 90-day age), rotating its month cursor only after
unfinished work is exhausted. State lives in the archived checkpoint, not ephemeral
GitHub storage. A partially refreshed month resumes without resetting completed
fresh pages. Old snapshots remain linked while a correction is collected.

Source failures are retained as failed jobs, never zero transactions. Only the
explicit provider error allowlist is eligible for bounded retries:

| Error | Earliest requeue |
| --- | --- |
| `upstream_timeout`, `upstream_network` | 30 minutes after the failure and last requeue |
| `upstream_http` | Six hours after the failure and last requeue |
| `upstream_quota` | Next KST day at 00:17; the remote same-day quota guard also applies |

At most five jobs are requeued per run, within the same total source-request budget;
at most three requeues per job/KST day. The retry ledger is part of the private
checkpoint. Original page and snapshot references remain untouched by requeue.
Collector's existing page-age validation can subsequently restart an expired
pagination sequence while retaining the immutable raw files. Unused requeues count
against the limit conservatively. The report includes the earliest known retry time.

Authentication, secret reflection, malformed data, checkpoint/remote uncertainty and
unknown errors never retry automatically. Operators must diagnose and explicitly
repair these cases. Missing/invalid legacy failure timestamps also require review.
Correction cursors advance only when the selected month has no pending, partial
**or failed** jobs. Thus a persistent failure holds the affected cursor visibly;
other backfill runs still execute. Cursor movement is not a claim of complete
national coverage: inspect all `pending`, `partial` and `failed` counts.
The existing registry's historical-code limitations still apply. Officetel data is
not collected into the apartment archive.

## Failures and recovery

An upstream quota/auth stop is archived when possible and causes a failed Actions
run. An ambiguous source reservation or archive write preserves the previous head
and retains remote ownership. Expiration, cancellation, timeout and the next cron
run **never automatically unlock it**. Diagnose durable reservations and run the
existing recovery procedure before restarting. No automatic refunds or repeated
possibly-executed calls are allowed. Disable the repository variable during
incident investigation if repeated blocked runs would be noisy. D1 daily write-limit
errors include the next UTC day at 00:17 as `next_retry_at`; provider daily-quota
errors use the next KST day at 00:17. These are earliest retry hints, not automatic
unlock instructions or guarantees of provider recovery.

HTTP-success responses containing XML quota codes (22/23) are also checked in the
restored checkpoint's call ledger before any new source call. A raw reservation may
correctly have status `stored` even when subsequent normalization reports quota;
checking only reservation error codes would miss this case.

## Pagination and bounded-run progress

Collector expires incomplete pagination after 15 minutes. The four-hour schedule
does not promise continuation of those pages. The automation records a durable
budget proof only when an entire run starts at page one and serves a single job,
stops at `run_budget`, and that job still needs pages. It records the first-page SHA,
reported page count and the bytes needed to attempt at least one further page under
Collector's existing response-size reservation rule.

On the next run, valid pages within 15 minutes can continue normally. After expiry,
if the configured request or response-byte limit is still demonstrably insufficient,
the run stops **before another source call** with
`automation_pagination_budget_insufficient`. The previous private head remains;
the provably zero-request attempt releases its own lease. An operator must inspect
the evidence and raise the appropriate budget within the existing hard bounds or
provide another verified collection strategy. The code never silently increases
request/byte caps and does not claim that increasing one limit guarantees completion.

A partial job started after other jobs consumed the run budget does not create this
proof: it may fit the next run's full budget and is allowed one full-budget retry.
If that dedicated retry is again insufficient, it produces the blocking proof.
Completed jobs clear their obsolete proof; changed first-page SHA cannot reuse old
evidence. Original raw pages and snapshots remain preserved throughout.

Logs contain only bounded counts, coverage and allowlisted error codes. Successful
local fixture tests prove orchestration and failure semantics; they do not prove
the secrets were provisioned, live APIs responded, schedule ran, twenty years were
collected or the public website was refreshed. Record the actual Actions run URL,
private checkpoint verification and public release verification separately.

## First acquisition and correction queues

`backfill` now selects only jobs whose `snapshot IS NULL`. A recent/history
correction keeps its last verified snapshot while it is pending, partial or
failed; it cannot consume this first-acquisition lane merely because its month
has higher priority. The existing regional order remains unchanged. Recent and
history lanes still select their month, including never-acquired jobs in that
month, and continue to verify corrections rather than discarding them.

Safe-error requeue and stale-pagination preflight use the same acquisition filter.
An oversized refresh therefore cannot block unrelated first acquisition, and an
unselected refresh does not consume the backfill retry allowance. Its own lane
still applies the original pagination/ownership/error safeguards. No job, old page,
snapshot or remote head is deleted or rewritten by this queue change.

The run report keeps `coverage` as the current processing status and adds
`acquisition`: `verified_snapshot_jobs`, `missing_snapshot_jobs`,
`refresh_pending_jobs`, `first_acquisition_failed_jobs` and `refresh_failed_jobs`.
For example, marking 466 old snapshots for refresh reduces the `complete` job
count but does not reduce acquired history. A failed correction remains a failure
and its older snapshot remains acquired; neither is interpreted as zero trades.
`work_complete` means the selected eligible queue was exhausted, not that failed
jobs or all nationwide historical-code gaps have been resolved.

The 2026-09-27 read-only production audit and budget estimates are in
[PROPERTY_COLLECTION_BUDGET_20260927.md](PROPERTY_COLLECTION_BUDGET_20260927.md).
This code change does not alter repository variables, cron, source quotas,
archive operations or the 500-request/64-MiB automation hard limits.

## Continuous 20-year target and source publication periods

The 2026-09-27 target is **240 completed KST contract months plus the provisional
current month**. `--months 241` is explicit in the scheduled workflow; smaller
explicit windows, including the older 121-month contract, remain valid. Migration
adds jobs and retains every prior original, call, snapshot and older month on
rollover. It never rewinds the archive head or resets acquired history.

`pipeline.real_estate_availability` records the official publication lower bounds
and evidence URL. The [MOLIT current disclosure guide](https://rt.molit.go.kr/pt/info/info.do?mobileAt=)
states sale disclosures from January 2006 and fixed-date rent disclosures from
January 2011, alongside lease reports from June 2021. This includes apartments
and officetels in the guide; each type retains its own collector checkpoint.
The [sale API](https://www.data.go.kr/data/15126468/openapi.do) and
[rent API](https://www.data.go.kr/data/15126474/openapi.do) metadata do not specify
a complete per-region historical time range. The floor is therefore a publication
policy, not proof that every later API query returns complete history.

New pre-source jobs use `source_unavailable` / `before_source_start`, with no
request reservation, raw response or snapshot. They are never `empty` or
`complete`. Selection and direct HTTP fetch both exclude them. Public partitions
carry null counts/timestamps, an empty transaction-reference list, and that
explicit status. `coverage.expected` includes all planned slots;
`coverage.source_unavailable` is a separate status count (absent means zero for
older releases). `acquisition.missing_snapshot_jobs` retains its original total
missing meaning; `eligible_jobs` and `missing_collectable_jobs` exclude unavailable
slots. `source_unavailable_jobs` makes that subtraction explicit. Refresh failures
retain their previous verified snapshot and are never reported as empty.

At September 2026, 256 current codes × 241 months × two trades is 123,392 slots:
13,312 rent slots precede January 2011, leaving 110,080 potentially collectable
slots. Current codes do not establish historical boundary coverage. This is a
preserved target ledger, not a collection-complete claim. The active nine-area
scope is **53,984 slots**, including **5,824 unavailable** and **48,160 potentially
collectable** slots. Remaining national slots are retained but not requested.
Officetel live automation remains
a separate connection task; this apartment workflow does not silently collect it.

Before remote migration, a local copy of the SHA-verified 61,952-slot /
8,503-acquired-job checkpoint was expanded with zero source or remote calls.
All 61,952 old jobs and 8,908 calls remained identical. SQLite grew from
17,059,840 to 23,060,480 bytes, below the existing 256 MiB archive-file cap.
New changed slices compressed to 2,080,971 bytes. Unavailable slots create no separate
raw/snapshot files or manifest entries. They are stored once, and retained in
the checkpoint so coverage and rectangular-window migration stay auditable.
The scoped baseline has 3,842 acquired jobs and 44,318 potentially collectable
missing jobs after the 241-month expansion. National acquired jobs remain 8,503;
they must not be presented as 8,503 jobs within the selected scope.
This is a local migration measurement, not a remote migration completion claim.
It does not guarantee space for subsequent derived snapshots or all 20 years.
Raw-capacity preflight, shard limits, daily quotas and fail-closed recovery remain
mandatory for the first live 241-month run and every later run.
