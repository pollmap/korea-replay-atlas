# Private apartment collection on GitHub Actions

`property-collect.yml` executes the real collector on a standard public-repository
Ubuntu runner. It is distinct from the older plan-only workflow. It does not
publish website data: the successfully audited **private D1 archive head** advances.
Public data still requires normalization, release audit, candidate upload and app
release pinning. Do not report that the public map auto-updates from this workflow.

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

The bootstrap default is **25 source requests and 16 MiB source response bytes**.
After a live verification, repository variables `PROPERTY_COLLECTION_MAX_REQUESTS`
(1–500) and `PROPERTY_COLLECTION_MAX_BYTES` (8–64 MiB, in bytes) can raise the
budget without code changes. Begin with 100 requests and measure actual archive
writes/time before raising it further. All bounds are validated before remote reads.
This is a conservative initial operating limit, not a 10-year completion promise.
The private archive has additional hydration, verification and upload traffic.
Existing provider-per-day accounting and D1 free storage/write guards remain in
force across all runners. A manual trigger does not bypass those guards.

## Work progression

The latest verified private head is restored selectively into a new UUID directory
on every run. Existing parent objects remain immutable. The 121-month plan expands
when a month changes, preserving every previously planned historical month and
original source response. Region ordering remains the existing user's priority.

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
the secrets were provisioned, live APIs responded, schedule ran, ten years were
collected or the public website was refreshed. Record the actual Actions run URL,
private checkpoint verification and public release verification separately.
