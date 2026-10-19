# Daily Windows Arm64 wheel monitoring pilot

Deployment home: **CoreCollective-dev/windows-on-arm-wg**.
Operational owner: **khmyznikov**.
Planned board: [khmyznikov Project 2](https://github.com/users/khmyznikov/projects/2).

## Current deployment

This is the **read-only pilot**, not the live issue tracker. The
[daily workflow](../.github/workflows/wheel-monitor.yml) runs at **15:17 UTC every
day**, and can be started manually. GitHub scheduling can be delayed or disabled;
this is a 24-hour polling cadence, not a guaranteed notification delivery time.

The implementation has no issue/comment/Project writing API and the workflow
does not request `issues: write`. No mentions are sent. The older
[cryptography monitor](../.github/workflows/monitoring_cryptography.yml) remains
active during comparison; it can still create its own per-job issues.

Five packages exercise different patterns:

| Package | Pattern | Current required coverage |
| --- | --- | --- |
| cryptography | Separate build/test workflows and composite steps | Release series 50.0 CPython 3.14/3.14t wheels; mainline 3.14/3.15 Windows Arm64 tests |
| pyproj | cibuildwheel | Mainline Windows Arm64 build/test and artifact upload |
| rpds-py | Rust/maturin | Mainline Windows ARM wheel job and upload |
| mypy | Separate wheel-building repository | Development builds of CPython 3.14/3.14t Windows Arm64 wheels |
| pyodbc | Irregular manually dispatched builder | Mainline Windows Arm64 wheel job; no inactivity deadline |

Selectors and baseline evidence are in [pilot.json](pilot.json). The full dated
[84-package inventory](inventory.json) is an onboarding backlog, not 84 verified
or activated monitors. Candidate inclusion does not establish Windows Arm64
support. An archived packaging repository remains marked as such.

## Running and testing

Python 3.12+ and the GitHub CLI are required. No extra Python packages are needed.
Authenticate `gh` for read access to public upstream Actions, or set `GH_TOKEN`.

From the repository root:

```powershell
python -m unittest discover -s monitoring\tests -v
python -m monitoring.monitor --output monitor-output
python -m monitoring.monitor --state monitor-output\state.json --output monitor-output
```

The second scan uses the previous dry-run baseline to suppress repeat planned
notifications. `--scheduled` advances the two-daily-poll threshold for operational
observation gaps; manual checks do not advance it.

Outputs:

- `report.json`: current per-target observations and planned transitions.
- `state.json`: next poll's baseline, pending runs, notification sequence and
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
- Selects explicit upstream events/refs; PRs are excluded.
- Reads attempt-specific jobs and merges unchanged jobs only from the same
  run/SHA. Jobs from a partial rerun cannot erase another target's failure.
- Uses required steps and job conclusions. A success-looking wheel marker cannot
  override a failed test. No logs or artifacts from upstream are executed.
- Keeps scopes separate. A release success does not clear mainline failure;
  another Python target cannot clear a free-threaded target's failure.
- Missing jobs or required steps produce Attention, not green. Known failures
  remain visible when coverage or API access disappears.
- Uses a 30-minute settlement window. Pending/cancelled runs are not recovery
  evidence. Newer established observations cannot be cleared by an older rerun.
- Recurring identical current conditions do not produce new planned transitions.
  Recurrence after recovery gets a new monotonic transition sequence.
- Regular test/development channels have explicit silence limits; push-only and
  manual/release builders do not get arbitrary inactivity alarms.
- Reads are bounded by 800 requests globally and 160 per package. Pagination
  and split time windows avoid silently truncating the GitHub search ceiling.

The pilot deliberately inspects a rolling **90-day** run-metadata horizon, even
on bootstrap, to compare infrequent builders and rediscover changed attempts.
It fetches jobs for the latest settled eligible run in each recovery scope,
not every historical job. Pending runs persist beyond that horizon. If there is
no usable baseline in the horizon, the result is Unknown, not a fabricated pass.
This is a current-health observer: an incident that opens and recovers entirely
between daily polls is not guaranteed to be reported.

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
3. Implement and test the live issue reconciler, participant opt-outs,
   crash-safe comment deduplication, and Project synchronization against fixtures.
4. Supply an authorized credential for user-owned Project 2. The current operator
   token lacks Projects scopes, and a repository `GITHUB_TOKEN` is not sufficient.
   Do not put tokens in configuration or issue comments.
5. Preview and approve permanent issue creation/adoption and board fields.
6. Disable the old cryptography issue creator **before** enabling live pings.
7. Enroll additional candidates in reviewed batches; never create 84 issues from
   an unverified discovery inventory.

No automatic date-based promotion exists. Finishing seven days does not silently
enable writes.

Rollback: disable the daily pilot workflow. Reports remain in artifacts until
retention expiry; the existing cryptography workflow is unaffected.
