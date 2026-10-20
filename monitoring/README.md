# Daily Windows Arm64 versus AMD64 wheel monitoring

Deployment home: **CoreCollective-dev/windows-on-arm-wg**.
Operational owner: **khmyznikov**.
Tracking direction: one master issue and package sub-issues. Projects is deferred.

## Current deployment

This deployment replaces the five-package pilot with the **all-package read-only
observer**. It includes all 84 audited dispositions, retaining explicit excluded,
universal, inactive and unresolved entries rather than claiming those are healthy
native monitors. It is not the live issue tracker. The
[daily workflow](../.github/workflows/wheel-monitor.yml) runs at **15:17 UTC every
day**, and can be started manually. GitHub scheduling can be delayed or disabled;
this is a 24-hour polling cadence, not a guaranteed notification delivery time.

The workflow grants only `contents: read` and `actions: read`. It has no issue-sync
job and never invokes the separately tested issue helper. The manual issue-bootstrap
workflow is not part of this deployment. No new issues, comments, labels, or
mentions are produced by this observer. The older
[cryptography monitor](../.github/workflows/monitoring_cryptography.yml) remains
active during comparison; it can still create its own per-job issues.
The operator explicitly chose to leave that legacy workflow active during this
rollout; it retains its own separate issue-creation behavior.

The original pilot subset exercised these patterns:

| Package | Pattern | Current required coverage |
| --- | --- | --- |
| cryptography | Separate build/test workflows and composite steps | Release series 50.0 CPython 3.14/3.14t wheels; mainline 3.14/3.15 Windows Arm64 tests |
| pyproj | cibuildwheel | Mainline Windows Arm64 build/test and artifact upload |
| rpds-py | Rust/maturin | Mainline Windows ARM wheel job and upload |
| mypy | Separate wheel-building repository | Development builds of CPython 3.14/3.14t Windows Arm64 wheels |
| pyodbc | Irregular manually dispatched builder | Mainline Windows Arm64 wheel job; no inactivity deadline |

The [fleet.json](fleet.json) registry contains all 84 package dispositions and
paired-platform selectors; [pilot.json](pilot.json) retains the five-package
subset. The [inventory](inventory.json) is discovery provenance, not proof of
coverage. Excluded, universal, archived and unresolved entries remain explicit.

## Current collection strategy: every run in the last 24 hours

The observer evaluates **all relevant runs of each configured
workflow from the last 24 hours**, including PRs and feature-branch pushes,
regardless of the overall success/failure conclusion. It no longer selects
only the latest mainline/release run. Pending runs remain tracked across polls.

Each PR is isolated by head repository and PR number. When GitHub omits the
association, an immutable head-commit stream is used rather than guessing the PR.
Forks and refs never recover each other's incidents. Cross-workflow baselines
must match source SHA, head repository, ref and event; the newest matching
baseline is used, not an older convenient success.

Every settled comparison is recorded in `observations`, including a failure
later fixed inside the same daily window. Latest results describe each stream;
the summary separately lists all qualifying failures seen in the window.
Replay fingerprints prevent the same overlapping window from reopening old
incidents. Runs with no relevant builds are skipped, not reported as failures.
An empty window preserves the last verified result and reports **No changes**.
Only a never-validated baseline stays Unknown/Needs setup. Historical health
always includes its evidence date; it is not presented as a fresh passing build.
Public upstream PR code is never downloaded or executed by the monitor.

## Baselines and last-known health

The report separates **Last verified health**, **Today's activity** and
**Last verified evidence**. Silence preserves both passing and failing results:
an open Arm-specific failure does not disappear after 24 hours. A new shared
failure, skipped AMD64 job or unavailable comparison does not clear it either.
API errors retain known health but show **Monitoring warning** and fail the poll.

Each reviewed channel can provide:

```json
{
  "baseline_run_id": 37695338325,
  "baseline_search_days": 90,
  "baseline_search_limit": 4
}
```

These are additional channel fields, not a complete registry entry. The observer
validates the seed's workflow and actual platform jobs with the same comparison
rules as daily runs. It also inspects bounded newer candidates on that seed's
branch/event, **without a success-only filter**. A more recent qualifying failure
wins over an old successful seed. The seed is not an override asserting success.
A deleted seed falls back to bounded historical discovery with a visible note.
For reviewed platform jobs, `historical_job_success: true` accepts a retained
**successful job conclusion** as baseline evidence even when detailed steps,
logs or wheel artifacts have expired. Evidence is labelled historical-job-conclusion
and remains tied to the actual run, source, platform and selector. This does not
invent a failed build or unit-test coverage, and it does not relax live failure
attribution. Artifacts are not required merely to remember that a build succeeded.

A verified historical Arm-only success can establish last-known health even if
an equivalent AMD64 job has not been located. In that case the report explicitly
shows **Alert coverage: comparator unavailable**. Baseline health and regression
alert coverage are separate: an Arm failure still cannot qualify without AMD64
success. This is how cryptography's unpaired ordinary-GIL 3.14 test and ddtrace's
Arm-only public builder are represented, rather than reporting their known
successful Arm builds as Unknown.

Baseline discovery runs only when a channel lacks initialized state, its
comparison definition changes, or an operator explicitly requests refresh.
`baseline_search: false` disables historical reads; ordinary new observations
still establish health. Read caps and incomplete/unreadable searches are explicit,
not success-shaped defaults. Search limits and historical inconclusive candidates
are recorded in the report. They do not imply complete historical incident coverage.

Last-known results are per channel, PR/ref/source stream and target. Different
PRs or branches cannot recover each other. A closed PR is removed from active
health and retained in `retired`, recording merged/closed-without-merge rather
than inventing a successful recovery. When GitHub omits the PR association,
commit-isolated evidence cannot safely be auto-retired as a closed PR.

Last-known active failures are not aged out with notification deduplication data.
They survive in the state artifact until superseded by applicable passing
evidence, explicitly retired, or invalidated by reviewed selector changes.
Historic bootstrap observations establish state but do not emit incident pings.

Unconfigured/blocked comparisons collect run metadata but avoid expensive job
downloads that cannot yield an admissible failure verdict. The reason is visible.

### Follow-up examples

- **NumPy:** native `numpy/numpy` CPython 3.13 wheel jobs include equivalent
  build/tests on both Windows platforms. The additional Windows no-BLAS CPython
  3.12 test job has no equivalent AMD64 full/no-BLAS cell and remains unpaired.
- **Numba:** real same-source AMD64 and Arm builders are located. Actual NumPy
  build dependencies differ; the supplied CPython 3.13 test logs resolve NumPy
  2.3.5 on ARM versus 2.3.0 on AMD64. The operator approved these configured
  Numba-specific build/runtime differences on 2026-10-20. They no longer block
  comparison; source identity, matching Python/ABI/stage and AMD64 success are
  still required. This approval does not waive other packages' checks.
- **regex:** a bounded, read-only log parser extracts cibuildwheel's per-interpreter
  completion/error markers from the one aggregate Windows job. A successful AMD64
  interpreter and a failed matching ARM interpreter can be compared independently.
  Missing/truncated/unsupported output is Unknown, never inferred from the parent
  job icon. ARM test coverage is not claimed on this AMD64 cross-compiling host.
- **statsmodels:** excluded at the operator's request; no public WoA build found.
- **ClickHouse Connect:** actual driver wheels are built by manually dispatched
  `publish.yml`. The reviewed run 38939939945 contains the Windows Arm64/AMD64
  driver pair. `publish_core.yml` builds the separate Rust core distribution;
  the supplied PR lint/binding workflows are Linux-only and do not provide
  Windows driver comparisons.
- **orjson / preshed / thinc / pyodbc:** successful historic platform jobs are
  admitted as dated baselines without requiring retained artifacts or step details.
- **XGBoost CPU:** native Arm construction is compared to `windows.yml`'s AMD64
  minimal CPU builder at the same source. Its script explicitly produces
  `xgboost-cpu`; the generic GPU-distribution test job is not used as a CPU test
  comparator. This channel is build-only.
- **Triton Windows:** the reviewed older input is `release/3.8.x-windows`, not
  `main-windows`. Its recorded source-ref rule now agrees with the actual job
  names; the 3.9 release remains separately isolated.

## Mandatory failure rule

**Only Windows Arm64 build/test failure AND equivalent Windows AMD64 success
can produce Broken.**

| Arm64 | AMD64 | Result |
| --- | --- | --- |
| Failed | Successful equivalent build/test job | Arm-specific failure |
| Failed | Failed, skipped, pending, missing or unverified | Not a confirmed Arm-specific failure; no failure notification |
| Passed | Passed or non-success | Not an Arm-specific failure |
| Neither platform built/tested | Neither platform built/tested | Ignore the inapplicable run; retain the last applicable observation |

Selectors preserve Python version, free-threaded/ABI and build/test roles.
Both sides must be Windows, with the correct architecture and source. An x86,
Linux or macOS job cannot be a Windows AMD64 baseline. Aggregate AMD64 baselines
require every configured equivalent job to succeed. Cross-workflow joins require
identical source SHA and reviewed ref and explicitly configured counterpart
workflows; they never select an unrelated successful run.

A selected build/test job failing before its required phase finishes is a
pipeline failure, even if that phase was skipped after setup failed. Upload-only
failures after successful build/tests are not automatically build/test failures.
An overall green workflow is not enough: the specific platform job conclusion is
needed. Expired step metadata may be replaced by that successful job conclusion
for historical baseline initialization only; it cannot establish the cause of a
failure. Cibuildwheel invocation alone does not establish unit-test coverage.

Healthy describes the observed Arm result, not a guarantee that AMD64 is green.
The report includes both platform job links/statuses. Unconfigured comparators
are prominently marked as alert-coverage gaps even where an Arm-only baseline
is known; guarded source-identity cases remain Unknown. In particular, NumPy's release
workflow SHA is not the SHA of its separately checked-out NumPy source.

The summary and `report.json` group packages by **Broken, Unknown, Healthy,
Paused**, alphabetically inside each group. The exceptional Attention category,
if used, appears alongside Unknown before Healthy.

Old single-platform failure state is explicitly rebased under this policy;
uncompared historical failures cannot remain Broken or create false recoveries.

## Running and testing

Python 3.12+ and the GitHub CLI are required. No extra Python packages are needed.
Authenticate `gh` for read access to public upstream Actions, or set `GH_TOKEN`.

From the repository root:

```powershell
python -m unittest discover -s monitoring\tests -v
python -m monitoring.monitor --output monitor-output
python -m monitoring.monitor --state monitor-output\state.json --output monitor-output
python -m monitoring.monitor --state monitor-output\state.json --output monitor-output --refresh-baselines
```

The second scan uses the previous baseline to suppress repeat planned
notifications. Shared failures and non-success AMD64 baselines do not open or
recover Arm-specific incidents. Observation/API errors remain explicit in the
report and process exit status; they are not upstream regression pings.

Outputs:

- `report.json`: last-known health, today's activity, historical initialization
  notes, all current per-target observations and planned transitions.
- `state.json`: durable last-known/retired results, initialization metadata,
  pending runs, notification sequence and
  last complete channel scan time.
- `summary.md`: human-readable report, also displayed in the Actions run summary.

In Actions, all three are saved in `wheel-monitor-report` for 90 days.
`--restore` downloads the newest available report from this workflow's completed
main-branch scheduled/manual runs. PR artifacts cannot become monitoring state.
No retained state after previous executions is an explicit error, not a silent
baseline reset. A failed scan still saves usable prior state and reports errors.
If a runner failed before saving an artifact, restore tries the prior execution
and prints a warning.

An upstream **Broken** result is report data, not a failure of the monitor
process. An API error, incomplete coverage, invalid registry, exhausted budget,
or corrupt state makes the monitoring run fail visibly. Other packages can still
be observed after one package fails.

## Pilot evaluation rules

- Lists all run conclusions, not only successful workflow runs.
- Includes PR and non-PR runs in the 24-hour window, with isolated source streams.
- Reads attempt-specific jobs and merges unchanged jobs only from the same
  run/SHA. Jobs from a partial rerun cannot erase another target's failure.
- Requires paired platform results, not a workflow's overall conclusion.
  A success-looking wheel marker cannot override a failed required test.
  No logs or artifacts from upstream are executed.
- Keeps scopes separate. A release success does not clear mainline failure;
  another Python target cannot clear a free-threaded target's failure.
- Missing jobs or required steps cannot establish a new Arm-specific failure.
  A proven prior paired failure remains historical evidence during API outages.
- Uses a 30-minute settlement window. Pending/cancelled runs are not recovery
  evidence. Newer established observations cannot be cleared by an older rerun.
- Recurring identical current conditions do not produce new planned transitions.
  Recurrence after recovery gets a new monotonic transition sequence.
- No runs in the window is not a failure or a generic inactivity alarm.
- Daily bulk reads are bounded by 1,200 requests globally and 320 per package
  (the all-PR scan exceeded the old 160-request package cap for pandas). Pagination
  and split time windows avoid silently truncating the GitHub search ceiling.

The daily collector uses a **24-hour** run window, plus persisted pending runs.
The one-time baseline search may look back 90 days (bounded by candidate/read caps);
reviewed seed IDs may be older and remain explicitly dated. Ninety days is also
the deduplication memory horizon, not a time-to-live for last-known failures.
Deleted or already-retention-expired evidence
and polling outages longer than the observation window can still leave gaps;
the monitor does not promise evidence it cannot retrieve.

## What remains gated

The larger [tracker specification](SPEC.md)
describes the target live system. The first deployment implements observation,
state persistence, and **notification planning only**. It does not implement
participant enumeration/opt-outs, permanent issue bootstrap, live comment
idempotency/chunking, or board synchronization. Do not infer these features from
the dry-run transition plan.

Before live rollout:

1. Collect at least seven days of daily reports and compare selected failures
   and reruns with upstream evidence. Re-run tests after registry changes.
2. Confirm target Python versions and retirement rules for each package. Do not
   auto-discover/broaden selectors when upstream changes naming or release series.
3. Finish any separately documented comparison gaps and participant notification
   implementation. All 31 original investigations now have a scoped configuration
   or explicit exclusion, including the approved Numba NumPy exception. The
   bootstrap/status helper is tested but not invoked by any deployed workflow.
4. Keep Projects deferred; no Project credentials are needed for issue tracking.
5. Preview and explicitly approve permanent issue creation/adoption.
6. Disable the old cryptography issue creator **before** enabling live pings.
7. Enroll additional candidates in reviewed batches; never create 84 issues from
   an unverified discovery inventory.

No automatic date-based promotion exists. Finishing seven days does not silently
enable writes.

Rollback: disable the daily pilot workflow. Reports remain in artifacts until
retention expiry; the existing cryptography workflow is unaffected.
