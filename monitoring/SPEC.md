# Windows Arm64 wheel monitoring and project issue tracker

Status: issue-based tracking approved and deployed on 2026-10-21; Projects remains deferred.

Date: 2026-10-19.

### Recovery reconciliation update (2026-10-24 UTC)

Open regressions are rechecked beyond the normal 24-hour discovery window so a
recovery cannot be missed merely because the next daily poll ran late. Follow-up
must stay within the same proven PR or non-PR ref/event/source scope. If GitHub
omits the run's PR association, repository/branch lookup plus actual PR commit
membership establishes identity; branch matching alone is insufficient.
Legacy failed commit-only keys migrate consistently in retained health and
notification memory. Different-PR passes do not clear the original failure.

### Live issue rollout (2026-10-21)

The user approved creating the master issue and one permanent sub-issue for each
non-paused package. [Master tracker](https://github.com/CoreCollective-dev/windows-on-arm-wg/issues/7)
has **76 package sub-issues**; the eight paused entries are excluded.
[tracking.json](tracking.json) persists the issue mapping. Daily runs edit the
bot-owned status comment and health labels and deliver deduplicated incident,
material-change and recovery comments with participant mentions and opt-outs.
The durable delivery outbox and preferences live in the monitoring state artifact;
bot-authored issue event markers reconcile ambiguous writes or lost acknowledgments.
The ordinary workflow has no bootstrap flag and cannot create/reassign issues.
Historical current regressions are announced once on enrollment. Upstream reads
remain read-only; Projects is deferred.

### Historical-success baseline and report ordering update (2026-10-21 UTC)

The operator accepts the specific historical platform job's success conclusion
as sufficient dated baseline evidence even when wheel artifacts, logs or steps
have expired. This does not relax the Arm-failed/AMD-successful alert rule or
infer test execution. Known Arm-only successes can establish health while an
explicit coverage warning explains that no equivalent AMD64 failure comparator
is available. Historical evidence cannot manufacture a new failed-stage verdict.
Sort the report by Broken, Unknown, Healthy, Paused, with package names
alphabetical inside each status group.

### Persistent baseline update (2026-10-20)

Separate last verified health from daily activity. No new applicable build means
**No changes**, preserving the last dated passing or failing result. Never-verified
targets remain Unknown/Needs setup. Inconclusive comparisons cannot recover a
known Arm-specific failure; API errors show Monitoring warning alongside retained
health. Optional `baseline_run_id` seeds and bounded newest-applicable historical
discovery establish initial state without a workflow-success filter. Historical
initialization does not send incident pings. PR/ref/source streams remain isolated;
closed PRs may be retired without claiming they passed. Active failure evidence
does not expire with the daily window or 90-day deduplication memory.

### Collection update (2026-10-20)

The user approved **all relevant runs in the past 24 hours, including PRs,
for every configured package**, to detect regressions before merge. Evaluate
every settled run, not merely the latest mainline success. PR/fork/ref histories
are isolated; if GitHub omits PR association, use the immutable head commit
rather than guessing. Cross-workflow baselines must match the source SHA, head
repository, event and ref. The original ARM-failed/AMD-successful rule is unchanged.
The all-package observer is being deployed read-only. Public issue creation,
status writes and participant notifications remain unapproved and disabled in
the new workflow; the legacy cryptography workflow is unchanged by request.

### Mandatory comparative policy update (2026-10-20 UTC)

Only a failed Windows Arm64 build/test job with a successful equivalent Windows
AMD64 baseline is an Arm-specific failure. AMD64 failure, skip, cancellation,
missing evidence or an unverified comparator cannot produce Broken. Arm64 success
is not a failure when AMD64 is non-success. Runs that build/test neither platform
are inapplicable, not coverage regressions.

Pair the same source, Python/ABI and build/test role. An explicitly configured
cross-workflow comparator must join on identical source SHA and reviewed ref.
Different build-source checkout SHAs cannot be hidden by equal workflow SHAs.
Legacy single-platform failure state must be rebased before using this policy.
All-package comparative observation is read-only; no new public issues are
approved. The planned tracking UI is a master issue with package sub-issues; Projects is
deferred and is not a prerequisite.

### First implementation milestone

The [daily read-only observer](../.github/workflows/wheel-monitor.yml) is deployed on
the tracker repository's main branch. It evaluates all 84 audited dispositions
and their configured platform comparisons, preserves report/state artifacts,
and plans notification transitions without posting comments. Exclusions and
unverified coverage remain explicit. See the [observer guide](README.md) for implemented behavior
and remaining gates. Live issue reconciliation, participant notifications, and
board synchronization are subsequent milestones, not enabled features of this
first deployment.

## 1. Goal and agreed scope

Maintain one long-lived tracking issue per Python package in
**CoreCollective-dev/windows-on-arm-wg**, linked under one master issue.
Projects is deferred. A scheduled GitHub Actions monitor checks selected
upstream **Windows Arm64 wheel builds and related tests**, updates each
package's health, and comments on its existing issue when something needs
attention or recovers.

For actionable notifications, explicitly mention the issue author, assignees,
and people who commented, excluding bots and people who opted out.

This spec is stored here for review. The future monitor, configuration, issues,
and board belong in the agreed tracker repository, not this repository.

### Outcomes

1. A single place to see which packages are healthy, broken, unobservable,
   missing Windows Arm64 coverage, or deliberately paused.
2. A permanent discussion and incident history for each package.
3. Useful notifications without one issue per failed job or repeated daily pings.
4. Evidence links identifying the affected workflow, run, attempt, job, and
   Python/matrix target.
5. Monitoring failures are visible and never misreported as upstream success.

### Non-goals for the first version

- Building wheels, testing installations locally, or changing upstream code.
- Automatically rerunning or dispatching upstream workflows.
- Creating issues or comments in upstream repositories.
- Proving that a wheel was uploaded to PyPI. Build/test health and publication
  availability are different signals; PyPI checks can be added separately.
- Monitoring unrelated Linux/macOS failures. Windows AMD64 is the required
  comparison baseline, and relevant PR runs are now explicitly in scope.
- AI-based failure diagnosis, external databases, or a continuously running service.

## 2. Inputs and eligibility

Use [the package/workflow tracker](https://github.com/khmyznikov/PyEnv-WoA-State/issues/1)
and the imported [dated audit inventory](inventory.json) as discovery seeds:
89 PyPI-linked packages, of which 84 have an explicit Actions wheel builder,
including a retained archived builder. The audit is a dated source inspection,
not a ready-to-run monitor registry or proof of Windows Arm64 support.

Create one registry entry and, after approval, one issue per eligible package.
Normalize Python distribution names using PyPI name normalization. The stable
identity is the **package**, not the source repository: a repository may produce
multiple packages, and a package may use a separate wheel-building repository.

Each of the 84 candidates must receive an onboarding outcome:

| Outcome | Monitoring behavior |
| --- | --- |
| Verified Windows Arm64 build/test target | Enable its verified channels. |
| Actions exists, but no Windows Arm64 target | Keep issue on board as Attention / Coverage missing; do not claim it is healthy. |
| Archived, retired, or deliberately inactive builder | Keep issue as Paused with a reason; do not generate recurring stale alerts. |
| Workflow/log access or package identity cannot be verified | Unknown / Needs setup; never guess a healthy result. |

The five packages without a qualifying Actions builder are not automatically
enrolled. They may be added later when a builder is identified.

Do not scrape the Markdown table during every scheduled scan. Import candidates
once, then maintain a reviewed, machine-readable registry. New upstream workflow
names, matrix layouts, and delegated repositories require reviewed updates.

## 3. What changes from the cryptography example

Reference: [the provided monitoring workflow](https://github.com/CoreCollective-dev/windows-on-arm-wg/blob/main/.github/workflows/monitoring_cryptography.yml).
The existing [PyEnv cryptography monitor](https://github.com/khmyznikov/PyEnv-WoA-State/blob/main/.github/workflows/monitor_cryptography_ci.yml)
uses the same general approach.

| Existing approach | Proposed behavior |
| --- | --- |
| Per-project inline shell and hard-coded matrix | One shared monitor with reviewed per-package selectors. |
| Lists only successful workflow runs | Inspect all relevant run conclusions; a failed workflow can contain the exact failure we need. |
| Creates an issue per run/job | Create or adopt one package issue; append incident transitions there. |
| A success marker can classify a failed job as flaky | Evaluate required build/test steps first. A wheel marker must not hide a failing test or other required step. |
| Log retrieval failure can become a broken build | Preserve known job failures, but classify missing evidence as a monitoring gap, not proof that compilation failed. |
| Fixed last-25-run scan | Paginated, checkpointed collection with explicit catch-up and incomplete-scan reporting. |
| Title-search-based deduplication | Stable package identity and machine event markers, reconciled before retrying writes. |
| Assignee notification | Explicit mentions of the agreed participant set, with opt-outs and notification deduplication. |

Retain useful ideas: public upstream polling, workflow-specific selectors,
bounded concurrency, retries, log evidence when needed, and an Actions summary.

## 4. Recommended architecture

Use one central scheduled workflow and a small shared Python monitor in the
tracker repository. Do not create 84 near-identical workflow files.

```text
Reviewed package registry
          |
Scheduled/manual Actions run
          |
Collect upstream runs -> select Windows Arm64 jobs -> evaluate evidence
          |
Compare observations with durable per-package state
          |
Reconcile one package issue -> post transition comments -> save state
          |
Synchronize issue labels and GitHub Projects health fields
```

### Components

| Component | Responsibility |
| --- | --- |
| Registry | Package identity, sources, concrete workflow callers, expected targets, evidence rules, cadence and pause settings. |
| Collector | Paginated GitHub reads, run-attempt reconciliation, retries, and bounded optional log downloads. |
| Evaluator | Deterministic channel health, matrix coverage, and failure/monitoring-gap classification. |
| Issue reconciler | Exactly one tracker issue per package; durable snapshot, participant discovery, and idempotent incident comments. |
| Board reconciler | Add issue items and update automation-owned health fields. |

Start with one process and a bounded read pool of five workers. Keep all issue,
state, and board writes serialized. Scale read-only collection into shards only
if measurements show this is necessary; do not introduce a database or service
just for the initial inventory.

Cross-repository polling is intentional: a `workflow_run` trigger in the tracker
repository does not subscribe it to arbitrary upstream repositories. A future
GitHub App/webhook integration could reduce latency, but would require upstream
installation/cooperation and is not required for version one.

## 5. Package registry contract

Registry entries are reviewed configuration, not user-editable issue commands.
Validate the entire registry before any mutations.

| Field | Requirement |
| --- | --- |
| `package` | Unique normalized PyPI distribution name. |
| `pypi_url`, `source_repositories` | Package and authoritative source links. |
| `issue_number` | Existing tracker issue to adopt, or null for approved bootstrap creation. Must belong to the tracker repository. |
| `enabled`, `pause_reason` | Explicit lifecycle control; a pause requires a reason. |
| `channels` | One or more build/test observations belonging to this package. |
| `channel.id` | Stable package-local identity, such as `wheel-win-arm64` or `tests-win-arm64`. |
| `channel.repository`, `workflow_path` | Concrete repository and executable caller workflow whose run records must be queried. A reusable template alone is insufficient. |
| `channel.events`, `refs` | Allowed events and branch/tag rules; resolve actual code refs, not just the default branch containing the workflow file. |
| `channel.job_selector` | Anchored regex over API-visible job names, verified against real recent runs. |
| `channel.targets` | Expected target identities, for example `cp314` and `cp314t`, and their job/step selectors. Required targets must be explicit. |
| `channel.required_steps` | Optional named-step selectors identifying build, test, repair, and artifact-upload requirements. |
| `channel.evidence` | Normal job/step conclusions; optional additional build-marker or wheel-artifact rules. |
| `channel.expectation` | Always expected, conditional on an explicit event/ref rule, or manual/release-only. |
| `channel.max_silence` | Required for regularly scheduled channels; disabled for irregular release/manual channels unless a specific build is expected. |
| `channel.baseline` | Why these selectors are correct, verified source/run links, and verification date. |
| `notification_owners` | Optional fallback human maintainers when the bot created the issue and no human participants exist. |

A channel can cover multiple targets, but each target has separate observations
and an independent incident identity. A successful CPython 3.14 job must not
clear a CPython 3.14 free-threaded failure.

Define recovery scopes explicitly when a channel includes several refs: mainline,
each maintenance branch, and each configured release series are separate scopes.
A successful release or maintenance-branch run must not clear a mainline failure.

Selectors must distinguish Windows Arm64 from Linux aarch64, macOS arm64, and
Windows x64. Do not rely on the substring `arm64` alone. Cross-compilation on an
x64 runner is valid if the verified build target is `win_arm64`.

Delegated builders must be traced to actual runs and the named distribution.
For example, the source repository's trigger and the repository doing wheel
construction may differ. Where one workflow builds multiple distributions,
filter the package-specific jobs; do not credit one sibling package's success.

## 6. Tracker issue and board

### One permanent issue per package

Suggested title: `[Wheel monitor] cryptography - Windows Arm64`.

The issue stays open while monitoring is enabled, including when healthy. A
failure reuses this issue; a recovery never closes it. Maintainers may close it
to retire monitoring; reconciliation treats a closed issue as Paused and does
not reopen it automatically. Resuming requires an authorized configuration
change and reopening/adoption of that issue.

Before creation, check configured mappings and enumerate tracker-labelled issues,
including closed issues. Match a stable package marker, not the issue title:

```text
<!-- wheel-monitor:package=cryptography -->
```

Only bootstrap may adopt/create issues. Ordinary scans must stop that package
with an operational error if its mapped issue is missing or has a conflicting
identity; they must not silently create a replacement. Multiple matching issues
require manual resolution. Existing unrelated human issues are never overwritten.

### Issue content and ownership

- Human-editable body: purpose, PyPI/source links, upstream discussions,
  responsible people, monitoring coverage, and opt-out instructions.
- One **bot-owned status comment**: current channel/target table, last checked,
  last success/failure, freshness, current incidents, and a versioned machine
  state block. Update this comment rather than rewriting human discussions.
- Bot-authored transition comments: durable, append-only incident history and
  participant mentions.
- Labels: `wheel-monitor` plus exactly one of `health:healthy`, `health:broken`,
  `health:attention`, `health:unknown`, or `health:paused`.

Existing manually maintained labels, assignees, issue titles and body content
must be preserved. Machine markers are trusted only in expected locations and,
for status/event comments, only from the configured bot identity.

### Optional future GitHub Projects board (deferred)

If separately approved later, use the supplied [khmyznikov Project 2](https://github.com/users/khmyznikov/projects/2),
linked to the tracker repository. This user-owned board replaces the originally
proposed organization-owned Project. Add each enrolled issue once after live
rollout is approved and board credentials have been verified.

| Field | Owner / behavior |
| --- | --- |
| Health | Automation-owned single select: Healthy, Broken, Attention, Unknown, Paused. Group the main board by this field. |
| Package | Automation-owned normalized distribution name. |
| Upstream | Automation-owned primary repository URL. |
| Last observed | Automation-owned date of latest accepted upstream evidence; precise timestamps remain in the issue. |
| Status, assignees, priority | Human-owned triage fields; do not confuse work status with build health. |

Recommended views: Health board, Broken/Attention table, and All packages.
Board configuration is not required for the current issue-first rollout. If a
future board integration is enabled, missing configuration is an error, not an
invitation to create a different project. Its synchronization failure must not undo a posted alert:
record it, fail the monitoring run, and retry synchronization next time.

## 7. Collection, ordering, and coverage

1. Poll every 24 hours, UTC cron `17 15 * * *`, plus manual runs.
   Scheduling off the hour reduces contention; it is not an exact-time SLA.
2. Each poll evaluates every relevant settled run created in the past 24 hours.
   An empty window is No changes with the last verified health/date retained;
   only targets without validated evidence remain Unknown/Needs setup.
3. Paginate all run/job/comment lists. Persist run/attempt/outcome fingerprints
   so overlapping manual or scheduled polls do not resend transitions.
4. Discover without success, branch, or PR-exclusion filters. Include relevant
   PRs, pushes, schedules, manual and release runs. Keep each PR/head repository
   and non-PR ref/event stream separate; do not let another PR recover an incident.
5. Persist pending run IDs and recheck them until terminal. A long-running job
   must not disappear merely because its creation time leaves the overlap window.
6. Evaluate the latest attempt of each discovered run and recheck pending IDs.
   Ninety days is the deduplication/notification-memory retention horizon, not
   an upstream 90-day search. Older reruns outside the observation window need
   explicit replay; no automatic coverage of those reruns is claimed.
7. Retrieve attempt-specific jobs. A partial rerun can omit jobs that were not
   rerun: carry their evidence from earlier attempts of the **same run and SHA**;
   never borrow evidence from a different commit. Missing prior evidence is Unknown.
8. Record every settled comparison, including failures later fixed in the same
   window. Within each PR/ref/target stream, order by run creation sequence
   or late completion time. A newer attempt supersedes the same run's older
   attempt, but rerunning an old run cannot clear a failure on a newer run.
9. Track discovery progress separately from unresolved evidence. Only advance a
   window after all its run-list pages are collected; persist pending/unknown
   observations for retry, not as successful observations.
10. If a read cap, API search ceiling, missing page, or outage prevents complete
    coverage, explicitly record the unscanned interval and catch-up cursor.
    Split busy time windows as needed. Do not declare recovery from partial data.

A successful overall workflow does not prove a selected job passed: account for
allowed-to-fail jobs and step outcomes. Conversely, an unrelated failed Linux job
must not make a successful Windows Arm64 channel Broken.

Missing expected targets are not successful targets. Explicit event/ref
conditions can make a target Not applicable for that run; unexpected absence
is a coverage gap. A new upstream workflow file or renamed job produces
Needs setup, not an automatically broadened regex.

## 8. Health evaluation and incident lifecycle

Maintain two separate concepts: **last established upstream result** and
**observation quality/freshness**. An API outage must not erase a known failure
or fabricate a recovery.

| Observation | Result / action |
| --- | --- |
| All required jobs/steps and configured evidence pass | Healthy for those targets. |
| Arm64 build/test job fails; equivalent AMD64 build/test baseline succeeds | Broken; record both jobs and the failed stage. |
| Arm64 fails but AMD64 also fails, skips or is unverified | Unknown comparison, not an Arm-specific failure; no failure ping. |
| Arm64 passes but AMD64 does not | Not an Arm-specific failure; show the AMD64 result separately. |
| Neither platform builds/tests on an intentionally gated run | Inapplicable; ignore for regression detection. |
| Build marker exists but required Arm tests fail | Broken only with a successful equivalent AMD64 test baseline. |
| Only an explicitly optional/non-gating step fails | Attention; record the warning without claiming a broken required build. |
| Required job/step skipped, neutral, or unexpectedly absent | Attention / Coverage gap, unless a verified applicability rule explains it. |
| Run queued, waiting for approval, or in progress | Pending; retain prior established result, subject to the configured timeout. |
| Superseded concurrency cancellation | Ignore for build health; it is not a successful observation. |
| Other cancellation | Inconclusive; retain prior result. Three cancellations within 24 hours without a newer valid result produce Attention. |
| Logs unavailable but job/step results suffice | Use those results; mark diagnostic detail unavailable. |
| Mandatory evidence unreadable, API denied, or scan incomplete | Observation gap; retain prior result, or Unknown if no result exists. |
| Expected channel silent beyond its configured cadence | Attention / Stale; retain the previous result and last-success timestamp. |
| Missing Windows Arm64 support or selectors never verified | Attention / Needs setup; no false green baseline. |
| Monitoring deliberately disabled or source retired | Paused, with reason. |

Required log evidence is only used where structured job/step results are
insufficient. A missing marker alone is not proof of a failed compiler invocation.
Optional log evidence must not downgrade a conclusively successful required job.

### Aggregation and notifications

- Per-package rollup: any established active Broken target wins; otherwise
  required coverage/freshness warnings produce Attention, unresolved required
  evidence produces Unknown, and only complete fresh success produces Healthy.
  All channels deliberately paused produces Paused. Display observation gaps
  alongside an established Broken result.
- Open an incident only after the first definitive paired Arm-specific failure, with a
  30-minute settlement grace from the **latest attempt's completion** before
  notifying. Re-fetch before publishing so a quick successful rerun can suppress
  a transient failure notification. Grace may end between scheduled polls.
- Incident identity is package + channel + stable target + condition family,
  plus recovery scope, not the changing run/job ID. A recurrence after recovery
  starts a new numbered episode. Aggregate multiple target changes into one
  package notification per scan.
- Subsequent equivalent failures update the status comment and occurrence count.
  Do not post a new mention for every run.
- Material changes are a newly affected required target, a new condition family,
  or a required-stage change such as build failure becoming test failure.
  A new SHA, different error wording, or another occurrence is not enough.
- Recover each incident only on newer valid evidence for the same target and
  scope, or a successful latest attempt of the same run. Full recovery requires
  all affected targets to pass. A removed target cannot silently count as fixed.
- Comment once on partial recovery and once on full recovery; mention
  participants for these transitions. No periodic reminder pings in version one.
- Report new coverage gaps on an otherwise eligible run immediately after
  settlement; report transient observation/API gaps only after two consecutive
  scheduled checks. A regularly scheduled channel defaults to 72 hours of allowed
  silence unless its verified cadence needs a different explicit threshold.
- Release/manual-only workflows have no generic inactivity alarm. If a
  configured release/build trigger implies that a required run should exist,
  allow 24 hours for that expected run before reporting missing coverage.

These timings are proposed configuration defaults, not commitments to exact
delivery times. Do not label a package "flaky" merely because a success-looking
line appeared in an otherwise failed job.

## 9. Participants and notification comments

At notification time, compute the union of:

1. Current issue author, if human.
2. Current assignees.
3. Human authors of all currently visible issue comments, across all pages.
4. Optional configured fallback owners when there are no human participants.

Exclude bot accounts, the monitor identity, deleted/unresolvable accounts, and
persisted opt-outs. Deduplicate by GitHub user ID, using the current login for
mentions. Do not add people merely because their usernames appear in quoted
text, logs, upstream discussions, or another participant's comment.

Provide exact self-service commands as standalone comment lines:
`/monitor unsubscribe` and `/monitor subscribe`. A command affects only its
authenticated author. The latest command wins; persist the preference so deleting
an opt-out comment does not silently resubscribe someone. Edits/deletions must not
erase a previously saved opt-out without a later explicit subscribe.

Read issue participants only when posting a notification or processing preference
changes. If participant/comment enumeration fails, leave that notification pending
and surface an operational error rather than posting an incomplete mention list.
People joining later receive future notifications, not retroactive re-pings.

GitHub has no general `@all` for issue participants. Watchers and silent
subscribers are not the agreed explicit-mention audience. Mentions request
notifications but cannot override a user's own GitHub notification settings.
When the bot is the issue author, configured human assignees/owners supply the
initial audience; enrollment must warn if none exist.

### Example incident comment

```text
Windows Arm64 regression: cryptography

Affected: wheel-win-arm64 / cp314t
Result: required wheel-build step failed
First observed: <UTC timestamp>

Upstream: pyca/cryptography, wheel-builder.yml
Run / attempt: <run link> / <attempt>
Job: <job link>
Commit / ref: <SHA> / <ref>
Evidence: <failed required step; short sanitized diagnostic if available>
Other monitored targets: <summary>

Participants: <computed @mentions>
This incident remains on this tracker issue; no upstream issue was created.

<!-- wheel-monitor:event=<deterministic-event-key> -->
```

Notification comments include all opted-in participants. If size requires
multiple comments, create deterministic numbered chunks and reconcile each
chunk before retrying. Do not silently truncate the mention list or repeatedly
notify earlier chunks after a partial failure.

## 10. Durable state and safe reconciliation

Use the bot-owned status comment as the initial durable state store. This avoids
a new state branch, commit-per-scan churn, or dependence on expiring Actions
caches/artifacts. Human-readable state and a compact versioned JSON block coexist.

Store channel/target results, run IDs/attempts, last successful scan windows,
pending observations, recent-run rerun index, incident IDs and transitions,
notification event/chunk markers, opt-outs, and the last successfully applied
registry revision. Store timestamps in UTC. Full logs and an unbounded run history
do not belong in issue comments.

Keep the snapshot bounded: retain active incidents and pending transitions, prune
resolved observation details after the 90-day rerun horizon, and leave historical
notification comments as the audit trail. If the bounded index cannot fit GitHub's
comment-size limits, mark that package as a capacity/setup error; do not truncate
coverage. A high-volume pilot must validate whether a different state store is
needed before fleet rollout.

### Write ordering

1. Validate configuration, issue identity, existing bot state, and complete reads.
2. Compute observations and deterministic proposed transitions without writing.
3. Reconcile any existing event/chunk markers using paginated bot-authored
   comments. A title search is not an authoritative deduplication mechanism.
4. Persist the refreshed snapshot with pending notification events before posting.
5. Create only missing event chunks. After an ambiguous timeout, re-read before
   retrying; do not blindly repeat an issue/comment creation request.
6. Mark delivered events in state; reconcile labels and board fields.

Only mark a notification delivered after every required chunk is acknowledged or
found. If the status update fails after a comment succeeded, the next run finds
its event marker and does not re-ping participants.

Derive each event key from package identity and a persisted monotonically
increasing transition sequence; chunk keys additionally include their index.
Freeze the transition payload and audience before sending the first chunk.
Retries reuse that pending event, while a later recurrence receives a new key.
An old failure's deduplication marker must never suppress a new episode.

Serialize scheduled, manual, and bootstrap writers with one workflow concurrency
group and `cancel-in-progress: false`. Skipped/replaced pending schedule executions
must not lose upstream data because discovery uses durable checkpoints. Multiple
independent writers are unsupported in version one.

Missing/corrupted/edited bot state is an explicit reconciliation error. Do not
reinitialize silently and resend the entire incident history. Recovery mode
requires a maintainer-approved state rebuild, using existing event markers where
available.

## 11. Authentication, safety, and operating limits

- Use `GITHUB_TOKEN` for local issue/comment writes with minimal repository
  permissions, typically `contents: read` and `issues: write`.
- The repository `GITHUB_TOKEN` cannot manage Projects. For the selected
  user-owned board, supply a separate authorized Project-capable credential
  (for example, an appropriately scoped PAT), restricted to this deployment.
  An organization Projects GitHub App is an alternative only if the board moves
  to an organization. Never store credentials in registry content.
- Verify public upstream run/job/log access during enrollment. Tracker-local
  `actions: read` permission does not grant access to private upstream repositories.
  If a private source is later required, obtain authorized installation/access
  explicitly; otherwise report Unknown/Access denied.
- Keep collection read-only upstream. No token needs upstream write permissions.
  Do not execute upstream code, downloaded scripts, artifacts, or log content.
- Pass registry/issue/log strings as data, never interpolated shell commands.
  Allowlist command syntax and authoritative bot markers. Do not use
  `pull_request_target` or untrusted issue content as privileged configuration.
- Prefer structured API data. Download logs only for diagnostics or configured
  evidence checks, at most 10 MiB per job. If a required check exceeds that cap,
  record evidence unavailable, not success.
- Publish links and at most 20 sanitized diagnostic lines / 4 KiB per event.
  Remove secrets, escape Markdown and suppress incidental `@mentions`; never
  mirror raw logs or signed log-download URLs into public issues.
- Retry transient failures with exponential backoff and jitter; respect
  `Retry-After`, rate-limit headers, and secondary limits. Do not treat every
  403 as rate limiting, or every 404 as proof that a workflow was deleted.
- Use a default bulk budget of 1,200 REST requests (320 per package) and a
  30-minute run timeout, preserving
  available quota for reconciliation. Bound work per package so a busy source
  cannot monopolize a scan. Record deferred work and resume it next time.
- Check rate-limit resources for the actual token; do not assume every token gets
  the same quota. Measure Projects GraphQL cost separately.
- Any incomplete scan, read/write/auth failure, invalid selector, or board failure
  produces a visible Actions summary and unsuccessful monitoring run. Isolate
  affected packages so other packages still reconcile.

The monitor must publish a last-completed-poll timestamp in its summaries/status.
GitHub schedules can be delayed, dropped, or disabled after public-repository
inactivity. A dead monitor cannot reliably report its own death: operational
ownership must include checking the workflow and GitHub failure notifications.
If a guaranteed heartbeat alert is needed, add an independent external watchdog
as a separately approved requirement.

## 12. Rollout and migration

1. **Rollout decisions (2026-10-19).** Daily polling, direct deployment of the
   read-only pilot to main, owner `khmyznikov`, and user Project 2 are approved.
   Current operator credentials lack Projects access; board writes remain blocked.
2. **Implement in the tracker repository.** Keep the current monitor intact
   until the new implementation has passed dry-run comparison.
3. **Pilot five patterns:** cryptography (build/test markers), a cibuildwheel
   package, a Rust/maturin package, mypy (external build repository), and an
   irregular/manual or archived builder. Verify real selectors and permission
   paths before enabling notifications.
4. **Dry-run for at least seven days.** Read and evaluate normally, but do not
   mutate issues, comments, labels, board items, or durable GitHub state. Preserve
   dry-run reports between pilot executions for comparison; never publish mentions.
5. **Approved bootstrap.** Preview issue creation/adoption and board additions,
   then enroll approved packages. Do not create 84 issues as a side effect of the
   first scheduled poll.
6. **Cut over cryptography.** Disable the old per-job issue creator before enabling
   new live notifications. Link existing incident issues from the permanent tracker;
   do not bulk-close or rewrite them automatically.
7. **Expand in batches.** Give all 84 candidates explicit verified/needs-setup/paused
   outcomes. A zero-match selector is not a completed successful enrollment.

Rollback: disable live writes or pause the new workflow; leave tracker issues
and history intact. Do not delete issues or re-enable the old issue creator
without deciding how to avoid duplicate notifications.

## 13. Acceptance scenarios

| Scenario | Required outcome |
| --- | --- |
| Same upstream failure scanned repeatedly | One package issue and one opening notification; status/occurrence count updates without repeated mentions. |
| Entire workflow fails on required Windows Arm64 job | Failure is found despite non-success overall run conclusion. |
| Linux job fails, required Windows Arm64 jobs pass | Package's Windows Arm64 build health is not Broken. |
| Allowed-to-fail Arm64 job contains a failed required test | Broken despite workflow success or a wheel-build success marker. |
| Job fails after wheel build during a required test/upload | Correct required-stage incident, not automatically "flaky." |
| Failure is fixed by rerun during settlement | No transient opening notification; latest-attempt success retained. |
| Rerun fails only one target | Reconcile that attempt with unchanged jobs from the same run/SHA; do not drop failed targets. |
| Old run is rerun successfully after a newer failure | Newer failure remains open; no false recovery. |
| One of two affected targets recovers | One partial recovery comment; issue remains Broken until all required incidents recover. |
| Job is renamed/removed or expected matrix cell disappears | Coverage/Needs setup warning; never green from an empty selection. |
| Archived builder or irregular release-only workflow is quiet | Paused or no generic stale alarm, according to configuration. |
| More than 100 jobs, runs, comments, or issues | Pagination preserves full configured coverage and all participants. |
| Runs remain active across several poll windows | Pending IDs continue to be checked; they are not lost at the time boundary. |
| API/log access denied or logs expired | Observation gap with known results preserved; no invented compile failure or recovery. |
| API budget or history search ceiling reached | Visible partial-scan error, saved catch-up cursor, and no success-shaped truncation. |
| Crash after posting a comment but before updating state | Next run finds event marker; no duplicate participant ping. |
| Ambiguous create/write timeout | Reconcile the existing resource before any retry. |
| One user comments repeatedly or has multiple roles | Mention that user once per notification. |
| Participant opts out, or later deletes the opt-out comment | No explicit future mention until a later self-authored subscribe command. |
| Participant enumeration fails | Notification remains pending; do not send a partial audience silently. |
| Comment body/log contains another username or a forged marker | Does not enroll that username or forge trusted bot state. |
| Tracker issue is manually closed | Pause rather than reopen or create a duplicate issue. |
| Projects write fails after alert succeeds | Preserve alert/state, fail monitor run, retry only board reconciliation. |
| Dry-run or malformed registry | No GitHub mutations. |

Testing must cover deterministic evaluation with recorded/synthetic API fixtures,
API pagination and permission failures, rerun ordering, state crash-recovery,
participant resolution, size limits, and board reconciliation. Compare planned
events to expected events, not just process exit codes.

## 14. Decisions still needed before live rollout

- Board field names/options and a verified Project-capable credential for
  [the selected user-owned board](https://github.com/users/khmyznikov/projects/2).
- Confirm initial human issue assignees; pilot operational owner is `khmyznikov`.
- Confirmation or adjustment of 30-minute settlement, 72-hour regular-channel
  silence, and two-poll observation-gap notification. Polling is now daily;
  two consecutive scheduled checks therefore mean approximately 48 hours.
- Per-package required Python targets, concrete selectors, expected cadence,
  gating steps, and whether a particular related test channel is mandatory.
- Whether future scope should include PyPI wheel availability, PR-only
  regressions, recurring reminders, or an independently monitored heartbeat.

## 15. Platform references

- [GitHub Actions event and schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
- [Workflow runs REST API, filters and search ceilings](https://docs.github.com/en/rest/actions/workflow-runs).
- [Attempt-specific jobs and job logs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt).
- [Issue comments REST API](https://docs.github.com/en/rest/issues/comments).
- [Projects automation and GitHub App authentication](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/automating-projects-using-actions).
- [GitHub API rate-limit resources](https://docs.github.com/en/rest/rate-limit/rate-limit).
