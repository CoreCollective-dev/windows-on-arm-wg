# Reviewed baseline health and comparative alert coverage

Verified read-only cloud snapshot: **2026-10-20T23:53:44Z**.
[Open action/report artifacts](https://github.com/CoreCollective-dev/windows-on-arm-wg/actions/runs/39517911822).
Grouped by Broken, Unknown, Healthy, Paused; alphabetical within each status.
Health is last verified evidence, not a promise that a new build ran today.
Historical platform job success is accepted without retained logs/artifacts.
An Arm-specific failure still requires equivalent successful Windows AMD64 evidence.

## Broken (2)

| Package | Today's activity | Last verified | Alert coverage / remaining gap |
| --- | --- | --- | --- |
| h5py | Arm-specific failure observed | [2026-10-20T06:43:48Z](https://github.com/h5py/h5py/actions/runs/39378483023) | Configured platform comparisons |
| lxml | No changes - no new applicable builds | [2026-10-15T11:11:54Z](https://github.com/lxml/lxml/actions/runs/38706693416) | Configured platform comparisons |

## Unknown (0)

No packages.

## Healthy (74)

| Package | Today's activity | Last verified | Alert coverage / remaining gap |
| --- | --- | --- | --- |
| aiohttp | Comparison inconclusive | [2026-10-11T01:05:59Z](https://github.com/aio-libs/aiohttp/actions/runs/38097142483) | Configured platform comparisons |
| argon2-cffi-bindings | No changes - no new applicable builds | [2026-10-15T09:41:17Z](https://github.com/hynek/argon2-cffi-bindings/actions/runs/38711816016) | Configured platform comparisons |
| bitarray | Comparison inconclusive | [2026-10-11T18:15:38Z](https://github.com/ilanschnell/bitarray/actions/runs/38156906966) | Configured platform comparisons |
| bitsandbytes | No changes - no new applicable builds | [2026-09-03T18:23:27Z](https://github.com/bitsandbytes-foundation/bitsandbytes/actions/runs/33789167923) | Configured platform comparisons |
| cffi | No changes - no new applicable builds | [2026-10-19T19:59:21Z](https://github.com/python-cffi/cffi/actions/runs/39301876443) | Configured platform comparisons |
| clickhouse-connect | No changes - no new applicable builds | [2026-10-16T17:41:53Z](https://github.com/ClickHouse/clickhouse-connect/actions/runs/38939939945) | Configured platform comparisons |
| contourpy | No changes - no new applicable builds | [2026-10-18T08:00:25Z](https://github.com/contourpy/contourpy/actions/runs/39106689605) | Configured platform comparisons |
| coverage | No changes - no new applicable builds | [2026-10-12T12:51:05Z](https://github.com/coveragepy/coveragepy/actions/runs/38248667792) | Configured platform comparisons |
| cramjam | No changes - no new applicable builds | [2026-10-01T09:11:12Z](https://github.com/milesgranger/cramjam/actions/runs/36821129850) | Configured platform comparisons |
| cryptography | Comparison inconclusive | [2026-09-30T14:41:26Z](https://github.com/pyca/cryptography/actions/runs/36713008517) | tests-win-arm64: ARM baseline known, but comparative failure alerts remain unavailable without an equivalent AMD64 job |
| cymem | No changes - no new applicable builds | [2026-10-09T22:50:41Z](https://github.com/explosion/cymem/actions/runs/38000198236) | Configured platform comparisons |
| ddtrace | Comparison inconclusive | [2026-10-20T22:39:19Z](https://github.com/DataDog/dd-trace-py/actions/runs/39508645824) | observed-8ab38fb0e6: ARM baseline known, but comparative failure alerts remain unavailable without an equivalent AMD64 job |
| duckdb | Passing builds observed | [2026-10-20T05:04:41Z](https://github.com/duckdb/duckdb-python/actions/runs/39359343522) | Configured platform comparisons |
| fastavro | Passing builds observed | [2026-10-20T13:17:42Z](https://github.com/fastavro/fastavro/actions/runs/39427172087) | Configured platform comparisons |
| frozenlist | Passing builds observed | [2026-10-20T06:29:24Z](https://github.com/aio-libs/frozenlist/actions/runs/39379920929) | Configured platform comparisons |
| gevent | No changes - no new applicable builds | [2026-09-16T15:59:21Z](https://github.com/gevent/gevent/actions/runs/35110072261) | Configured platform comparisons |
| greenlet | No changes - no new applicable builds | [2026-08-03T13:09:03Z](https://github.com/python-greenlet/greenlet/actions/runs/30811699991) | Configured platform comparisons |
| h3 | Builds pending | [2026-10-19T23:38:05Z](https://github.com/uber/h3-py/actions/runs/39341701115) | Configured platform comparisons |
| hiredis | No changes - no new applicable builds | [2026-10-15T18:00:50Z](https://github.com/redis/hiredis-py/actions/runs/38772366767) | Configured platform comparisons |
| ijson | Passing builds observed | [2026-10-20T09:53:07Z](https://github.com/ICRAR/ijson/actions/runs/39402679463) | Configured platform comparisons |
| jiter | No changes - no new applicable builds | [2026-10-15T11:17:23Z](https://github.com/pydantic/jiter/actions/runs/38724189063) | Configured platform comparisons |
| libcst | No changes - no new applicable builds | [2026-08-11T05:54:34Z](https://github.com/Instagram/LibCST/actions/runs/31461508173) | Configured platform comparisons |
| llvmlite | Comparison inconclusive | [2026-10-20T18:26:34Z](https://github.com/numba/llvmlite/actions/runs/39459192091) | Configured platform comparisons |
| logbook | No changes - no new applicable builds | [2026-10-18T14:22:18Z](https://github.com/getlogbook/logbook/actions/runs/39134666975) | Configured platform comparisons |
| marisa-trie | No changes - no new applicable builds | [2026-07-20T21:17:02Z](https://github.com/pytries/marisa-trie/actions/runs/29778673854) | Configured platform comparisons |
| markupsafe | No changes - no new applicable builds | [2026-10-02T23:07:26Z](https://github.com/pallets/markupsafe/actions/runs/37073936755) | Configured platform comparisons |
| matplotlib | Passing builds observed | [2026-10-20T21:27:39Z](https://github.com/matplotlib/matplotlib/actions/runs/39498014354) | Configured platform comparisons |
| maxminddb | Passing builds observed | [2026-10-20T14:44:05Z](https://github.com/maxmind/MaxMind-DB-Reader-python/actions/runs/39440339772) | Configured platform comparisons |
| ml-dtypes | Passing builds observed | [2026-10-20T23:21:12Z](https://github.com/jax-ml/ml_dtypes/actions/runs/39509065980) | Configured platform comparisons |
| mmh3 | No changes - no new applicable builds | [2026-09-30T16:34:46Z](https://github.com/hajimes/mmh3/actions/runs/36741097485) | Configured platform comparisons |
| msgpack | No changes - no new applicable builds | [2026-10-19T05:18:05Z](https://github.com/msgpack/msgpack-python/actions/runs/39198367381) | Configured platform comparisons |
| msgspec | Comparison inconclusive | [2026-10-20T07:11:14Z](https://github.com/msgspec/msgspec/actions/runs/39383423042) | Configured platform comparisons |
| multidict | Comparison inconclusive | [2026-10-09T20:39:21Z](https://github.com/aio-libs/multidict/actions/runs/37929221966) | Configured platform comparisons |
| mypy | Passing builds observed | [2026-10-20T13:23:02Z](https://github.com/mypyc/mypy_mypyc-wheels/actions/runs/39417338395) | Configured platform comparisons |
| nh3 | No changes - no new applicable builds | [2026-10-17T02:25:27Z](https://github.com/messense/nh3/actions/runs/38994482488) | Configured platform comparisons |
| numba | Passing builds observed | [2026-10-20T13:25:10Z](https://github.com/numba/numba/actions/runs/39419581183) | Configured platform comparisons |
| numexpr | No changes - no new applicable builds | [2026-10-13T09:15:18Z](https://github.com/pydata/numexpr/actions/runs/38373633472) | Configured platform comparisons |
| numpy | Comparison inconclusive | [2026-10-20T08:15:27Z](https://github.com/numpy/numpy/actions/runs/39382270034) | Configured platform comparisons |
| onnx | Builds pending | [2026-10-19T06:09:06Z](https://github.com/onnx/onnx/actions/runs/39207153545) | Configured platform comparisons |
| onnxruntime-qnn | Passing builds observed | [2026-10-20T22:21:34Z](https://github.com/onnxruntime/onnxruntime-qnn/actions/runs/39398034284) | Configured platform comparisons |
| orjson | No changes - no new applicable builds | [2026-09-29T13:31:08Z](https://github.com/ijl/orjson/actions/runs/36574739590) | Configured platform comparisons |
| pandas | Passing builds observed | [2026-10-20T04:07:23Z](https://github.com/pandas-dev/pandas/actions/runs/39365271941) | Configured platform comparisons |
| preshed | No changes - no new applicable builds | [2026-03-23T08:52:01Z](https://github.com/explosion/preshed/actions/runs/23428708476) | Configured platform comparisons |
| propcache | Passing builds observed | [2026-10-20T06:37:01Z](https://github.com/aio-libs/propcache/actions/runs/39380619090) | Configured platform comparisons |
| psutil | Passing builds observed | [2026-10-20T05:35:11Z](https://github.com/giampaolo/psutil/actions/runs/39375449063) | Configured platform comparisons |
| pycares | Comparison inconclusive | [2026-10-20T09:08:38Z](https://github.com/saghul/pycares/actions/runs/39392490402) | Configured platform comparisons |
| pycryptodome | No changes - no new applicable builds | [2026-10-19T10:39:02Z](https://github.com/Legrandin/pycryptodome/actions/runs/39231924547) | Configured platform comparisons |
| pymongo | Passing builds observed | [2026-10-20T05:55:38Z](https://github.com/mongodb/mongo-python-driver/actions/runs/39375808888) | Configured platform comparisons |
| pyodbc | No changes - no new applicable builds | [2025-10-17T17:49:19Z](https://github.com/mkleehammer/pyodbc/actions/runs/18600198971) | Configured platform comparisons |
| pyproj | No changes - no new applicable builds | [2026-10-19T14:06:51Z](https://github.com/pyproj4/pyproj/actions/runs/39259020458) | Configured platform comparisons |
| pyyaml | No changes - no new applicable builds | [2026-10-07T22:37:45Z](https://github.com/yaml/pyyaml/actions/runs/37695338325) | Configured platform comparisons |
| regex | No changes - no new applicable builds | [2026-09-09T21:00:26Z](https://github.com/mrabarnett/mrab-regex/actions/runs/34397217583) | Configured platform comparisons |
| rpds-py | Passing builds observed | [2026-10-20T10:32:56Z](https://github.com/crate-py/rpds/actions/runs/39407316148) | Configured platform comparisons |
| safetensors | No changes - no new applicable builds | [2026-09-22T16:43:59Z](https://github.com/safetensors/safetensors/actions/runs/35755530521) | Configured platform comparisons |
| scikit-image | No changes - no new applicable builds | [2026-10-18T12:16:14Z](https://github.com/scikit-image/scikit-image/actions/runs/39123469513) | Configured platform comparisons |
| scikit-learn | Passing builds observed | [2026-10-20T04:47:43Z](https://github.com/scikit-learn/scikit-learn/actions/runs/39366421259) | Configured platform comparisons |
| scipy | Passing builds observed | [2026-10-20T04:02:08Z](https://github.com/scipy/scipy/actions/runs/39364529815) | Configured platform comparisons |
| sentencepiece | Passing builds observed | [2026-10-20T12:26:39Z](https://github.com/google/sentencepiece/actions/runs/39417210704) | Configured platform comparisons |
| shap | Passing builds observed | [2026-10-20T10:12:05Z](https://github.com/shap/shap/actions/runs/39400364419) | Configured platform comparisons |
| spacy | No changes - no new applicable builds | [2026-08-24T09:57:24Z](https://github.com/explosion/spaCy/actions/runs/32706299673) | Configured platform comparisons |
| thinc | No changes - no new applicable builds | [2026-03-23T07:11:19Z](https://github.com/explosion/thinc/actions/runs/23424966006) | Configured platform comparisons |
| thriftpy2 | No changes - no new applicable builds | [2026-09-05T07:04:03Z](https://github.com/Thriftpy/thriftpy2/actions/runs/33950768644) | Configured platform comparisons |
| tokenizers | No changes - no new applicable builds | [2026-10-19T16:47:20Z](https://github.com/huggingface/tokenizers/actions/runs/39286932278) | Configured platform comparisons |
| tornado | Passing builds observed | [2026-10-20T21:14:21Z](https://github.com/tornadoweb/tornado/actions/runs/39495026546) | Configured platform comparisons |
| tree-sitter-language-pack | No changes - no new applicable builds | [2026-10-17T09:27:33Z](https://github.com/xberg-io/tree-sitter-language-pack/actions/runs/38997473342) | Configured platform comparisons |
| triton-windows | Passing builds observed | [2026-09-22T13:36:46Z](https://github.com/triton-lang/triton-windows/actions/runs/35716211357) | Configured platform comparisons |
| ujson | No changes - no new applicable builds | [2026-10-05T19:43:23Z](https://github.com/ultrajson/ultrajson/actions/runs/37362538631) | Configured platform comparisons |
| winloop | No changes - no new applicable builds | [2026-10-10T19:51:17Z](https://github.com/Vizonex/Winloop/actions/runs/38081098543) | Configured platform comparisons |
| wrapt | No changes - no new applicable builds | [2026-10-19T02:21:49Z](https://github.com/GrahamDumpleton/wrapt/actions/runs/39187412375) | Configured platform comparisons |
| xgboost-cpu | Passing builds observed | [2026-10-20T19:45:01Z](https://github.com/dmlc/xgboost/actions/runs/39411615331) | Configured platform comparisons |
| yarl | Comparison inconclusive | [2026-09-15T19:35:32Z](https://github.com/aio-libs/yarl/actions/runs/35007891886) | Configured platform comparisons |
| zope-interface | No changes - no new applicable builds | [2026-10-18T16:28:14Z](https://github.com/zopefoundation/zope.interface/actions/runs/39138596230) | Configured platform comparisons |
| zopfli | No changes - no new applicable builds | [2026-06-10T09:06:22Z](https://github.com/fonttools/py-zopfli/actions/runs/27265017989) | Configured platform comparisons |
| zstandard | Passing builds observed | [2026-10-20T18:45:36Z](https://github.com/indygreg/python-zstandard/actions/runs/39471276396) | Configured platform comparisons |

## Paused (8)

| Package | Today's activity | Last verified | Alert coverage / remaining gap |
| --- | --- | --- | --- |
| bcrypt | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| faiss-cpu | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| google-re2 | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| gymnasium | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| pymupdf | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| pynacl | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| srsly | Not monitored | Not applicable / not established | Explicitly paused/excluded |
| statsmodels | Not monitored | Not applicable / not established | Explicitly paused/excluded |

## Latest investigation findings

### clickhouse-connect

Run 38939939945 establishes the actual clickhouse-connect driver pair in publish.yml: both Windows jobs and their Build wheels steps succeeded. Both checkout logs resolve to 7fed155ee1977da56a74350399c9210e5234018f, ref v1.11.0, event workflow_dispatch. Root setup.py names clickhouse-connect; logs show clickhouse_connect-1.11.0 wheels with win_arm64 and win_amd64 tags. Both jobs built ordinary CPython 3.10–3.15 plus free-threaded 3.14t/3.15t, with matching interpreter/ABI tags. The AMD64 job additionally builds win32 and PyPy 3.10 wheels; those are extra coverage, not Arm64 comparators. The eight Arm64 configurations all have successful AMD64 counterparts. Compare the named Build wheels stage, not artifact upload/publication or unrelated test stages.

Run 38936133665 is publish_core.yml: supplied job 117092001421 builds clickhouse-connect-core, explicitly named by rust/ch-core-py/pyproject.toml and selected through MATURIN_MANIFEST. Its separate portable driver qualification wheel is py3-none-any on Ubuntu, not a native Windows driver pair. Run 38936051914 is binding_ci.yml, with PR/push/manual triggers; supplied job 117092047810 really builds a driver wheel, but only on ubuntu-26.04, so it cannot establish Windows equivalence. Run 38936051886 is on_push.yml, whose PR/push/manual lint/test jobs are likewise Ubuntu-only. The latter three supplied runs use d5a1d85c8c4ae119d6a65af219533e4cea059af3; their conclusions must not be joined to the driver dispatch at a different source/event. publish.yml has identical workflow blob dc11b22ec65877d48d238573d7625781077ddebd at both inspected commits.

Enroll only the verified Windows driver publisher. It declares workflow_dispatch only, so there is no verified PR Windows-driver channel in these sources. Collect all applicable dispatch refs/runs in the rolling 24-hour window, keeping source/ref/event/run context separate; no latest-success-only filter. The October 16 successful pair is historical initialization evidence, not a new observation inside the October 20 window. Historical successful per-job conclusions remain sufficient if detailed steps/logs expire. Missing detail is not failure. Report a regression only for an actual Arm64 build failure with successful comparable Windows AMD64 interpreter/ABI/build-stage evidence from the same source/ref/event; shared failures, setup/upload failures, skipped jobs and absent comparisons are not proven Arm-specific regressions. No alternative workflow search was necessary after resolving the supplied driver run.

### cryptography

User's supplied 3.14 WoA job 109937602900 is a successful wheel build already paired with successful AMD64 job109937602987 in the same release run36713008517. The previous Unknown was the separate ordinary-GIL3.14 tests-nocoverage target, not the wheel. CI run39243624070 has AMD64 tests at3.9,3.14t,3.15,3.15t but no equivalent ordinary3.14 test job. Historical ARM success now establishes health; comparative failure alerts remain unavailable only for that unpaired3.14 test target. Do not substitute AMD64 3.14t or3.9.

### ddtrace

WoA wheel discovery was not the problem. Reviewed build_deploy.yml run39315120078 has four successful Arm wheel jobs. Its invoked build_python_3.yml explicitly uses cibuildwheel --platform windows --archs ARM64, emitting only cp311/cp312/cp313/cp314-win_arm64 jobs. No equivalent Windows AMD64 job is in that public workflow graph. Historical job success now supplies the baseline with an explicit AMD64-alert-coverage warning; an arbitrary Linux or older AMD64 success is not substituted.

### orjson

Accepted user run36574739590, source6ae706bf0e1234d2478f084b3736ab687566fa92 on try. It has successful matched Windows ARM64/AMD64 jobs for3.11-3.15, including supplied ARM3.14 job109427339253 and AMD3.14 job109427338986. Artifacts are not required for baseline health. The actual try ref remains isolated from master/PR streams.

### preshed

Accepted successful historical ARM job68149463975 and AMD job68149463969 in supplied release-v3.0.13 run23428708476. Retained job success is sufficient for the baseline despite empty historical steps/expired artifacts. Missing detail is never evidence of failure.

### pyodbc

Applied the same approved historical-job-success rule to retained Windows ARM64/AMD64 job conclusions in run18600198971. A dated passing baseline no longer depends on artifacts or step retention; manual inactivity is No changes.

### thinc

Applied the same rule to both retained successful native Windows jobs in release-v8.3.13 run23424966006. Baseline accepted despite expired detailed steps; no unexecuted tests inferred.

### triton-windows

Corrected the older channel's source_ref from main-windows to release/3.8.x-windows, matching its actual selectors and seed35716211357. The separate3.9 source input remains distinct. Source captures compare actual resolved code SHAs, with latest coherent rerun attempts; no older branch pass can clear another source-ref failure.

### xgboost-cpu

Resolved a real same-source CPU-only AMD64 builder in windows.yml run39279943131 job118152122558 versus ARM run39279943209 job118152123680 (source dd59cc41c0d72ac91d225d4638b38bcf8c3b2025). AMD64 minimal job invokes build-win64.ps1 -variant cpu; that script explicitly renames/builds xgboost-cpu. Compare construction only. Removed the ARM generic installed-xgboost test target because its sibling GPU-package test is not a verified CPU-wheel comparator.

## What is still useful to investigate

- **cryptography:** find an ordinary-GIL Windows AMD64 CPython3.14 full test baseline equivalent to the ARM3.14 tests-nocoverage job, or deliberately retire that unpaired test alert surface. The3.14 wheel itself is verified on both platforms.
- **ddtrace:** identify the actual observable Windows AMD64 wheel-producing workflow/CI and source linkage for the matching Python/ABI variants. The public ARM builder's matrix does not include it.
These are alert-coverage gaps, not Unknown historical health. No artifacts need to be recovered for already successful reviewed historical jobs.

No issues, comments, participant pings or Project mutations were performed.
Collection errors in the verified run: 0.
