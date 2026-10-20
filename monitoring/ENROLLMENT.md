# Paired Windows monitoring: all relevant runs in the past 24 hours

Read-only snapshot: 2026-10-20T18:00:59Z. 370 runs with comparisons; 233 PR runs.
ARM-specific failure requires ARM64 build/tests failed AND equivalent Windows AMD64 succeeded.
No public issue or workflow writes were performed.

| Package | Comparison readiness | 24-hour health | Comparison observations |
| --- | --- | --- | ---: |
| aiohttp | paired | Unknown | 9 |
| argon2-cffi-bindings | paired | Unknown | 0 |
| bcrypt | no-arm-target | Paused | 0 |
| bitarray | paired | Healthy | 2 |
| bitsandbytes | paired | Unknown | 0 |
| cffi | paired | Healthy | 7 |
| clickhouse-connect | unresolved | Unknown | 0 |
| contourpy | paired | Unknown | 0 |
| coverage | paired | Unknown | 0 |
| cramjam | paired | Unknown | 0 |
| cryptography | partial | Unknown | 16 |
| cymem | paired | Unknown | 0 |
| ddtrace | unresolved | Unknown | 880 |
| duckdb | paired | Healthy | 12 |
| faiss-cpu | inactive | Paused | 0 |
| fastavro | paired | Healthy | 5 |
| frozenlist | paired | Healthy | 6 |
| gevent | paired | Unknown | 0 |
| google-re2 | excluded | Paused | 0 |
| greenlet | paired | Unknown | 0 |
| gymnasium | universal | Paused | 0 |
| h3 | paired | Healthy | 20 |
| h5py | paired | Broken | 2 |
| hiredis | paired | Unknown | 0 |
| ijson | paired | Healthy | 3 |
| jiter | paired | Unknown | 0 |
| libcst | paired | Unknown | 0 |
| llvmlite | paired | Unknown | 0 |
| logbook | paired | Unknown | 0 |
| lxml | paired | Unknown | 0 |
| marisa-trie | paired | Unknown | 0 |
| markupsafe | paired | Unknown | 0 |
| matplotlib | paired | Healthy | 3 |
| maxminddb | paired | Healthy | 10 |
| ml-dtypes | paired | Healthy | 1 |
| mmh3 | paired | Unknown | 0 |
| msgpack | paired | Unknown | 0 |
| msgspec | paired | Unknown | 16 |
| multidict | paired | Unknown | 5 |
| mypy | paired | Healthy | 2 |
| nh3 | paired | Healthy | 1 |
| numba | paired (approved NumPy exception) | Healthy (Numba-only check, 2026-10-20T18:43:16Z) | 12 |
| numexpr | paired | Healthy | 2 |
| numpy | paired | Unknown | 12 |
| onnx | paired | Unknown | 0 |
| onnxruntime-qnn | paired | Unknown | 0 |
| orjson | paired | Unknown | 0 |
| pandas | paired | Healthy | 7 |
| preshed | paired | Unknown | 0 |
| propcache | paired | Healthy | 24 |
| psutil | paired | Healthy | 50 |
| pycares | paired | Unknown | 6 |
| pycryptodome | paired | Unknown | 0 |
| pymongo | paired | Healthy | 1 |
| pymupdf | excluded | Paused | 0 |
| pynacl | excluded | Paused | 0 |
| pyodbc | paired | Unknown | 0 |
| pyproj | paired | Unknown | 0 |
| pyyaml | paired | Unknown | 0 |
| regex | paired | Unknown | 0 |
| rpds-py | paired | Healthy | 13 |
| safetensors | paired | Unknown | 0 |
| scikit-image | paired | Unknown | 0 |
| scikit-learn | paired | Healthy | 4 |
| scipy | paired | Unknown | 9 |
| sentencepiece | paired | Healthy | 1 |
| shap | paired | Healthy | 1 |
| spacy | paired | Unknown | 0 |
| srsly | universal | Paused | 0 |
| statsmodels | excluded | Paused | 0 |
| thinc | paired | Unknown | 0 |
| thriftpy2 | paired | Unknown | 0 |
| tokenizers | paired | Unknown | 0 |
| tornado | paired | Healthy | 1 |
| tree-sitter-language-pack | paired | Unknown | 0 |
| triton-windows | paired | Unknown | 1 |
| ujson | paired | Unknown | 0 |
| winloop | paired | Unknown | 0 |
| wrapt | paired | Unknown | 0 |
| xgboost-cpu | unresolved | Unknown | 4 |
| yarl | paired | Unknown | 2 |
| zope-interface | paired | Unknown | 0 |
| zopfli | paired | Unknown | 0 |
| zstandard | paired | Healthy | 5 |

## Four follow-ups

### numpy

The new native numpy/numpy PR example resolves wheel-build monitoring without the numpy-release mutable-source problem. CPython 3.13 Windows Arm64 and AMD64 Build wheels jobs both succeed in one source-commit run, and the pinned cibuildwheel configuration runs tools/wheels/cibw_test_command.sh on both. The additional windows.yml example really tests Arm64 CPython 3.12/full/no-BLAS, but its AMD64 candidates are 3.12/fast/openblas32 and 3.15/full/openblas64, so that extra test surface is retained as unpaired evidence outside active failure monitoring, not incorrectly compared.

[Supplied Arm64 CPython 3.13 wheel build/test](https://github.com/numpy/numpy/actions/runs/39440763314/job/118689655017); [Equivalent AMD64 CPython 3.13 wheel build/test](https://github.com/numpy/numpy/actions/runs/39440763314/job/118689655130); [Additional Arm64 full/no-BLAS CPython 3.12 tests, no equivalent AMD64 cell](https://github.com/numpy/numpy/actions/runs/39440763449/job/118689653900); [Pinned actual wheel-test configuration](https://github.com/numpy/numpy/blob/35ad3af05edf8c59ba60169fba68303ae1a12383/pyproject.toml#L305)

### numba

The operator approved Numba's configured NumPy version mismatch on 2026-10-20. The three Python 3.12/3.13/3.14 build pairs and Python 3.13 runtime-test pair are enabled for comparison despite their NumPy differences. Same-source/ref/event, matching Python/ABI, equivalent build/test stage and successful AMD64 evidence remain mandatory. This is a Numba-specific exception, not a global relaxation.

Targeted post-approval validation at **2026-10-20T18:43:16Z** examined 12 observations from three PR runs in 10 API requests, with no collection errors. All Arm observations passed. One AMD64 test was still pending; that is not an Arm failure and was not treated as a completed successful baseline. The other table rows retain the earlier fleet snapshot; only Numba was rechecked for this scoped approval.

[Supplied actual WoA wheel builder](https://github.com/numba/numba/actions/runs/39452429967); [Located same-source AMD64 builder](https://github.com/numba/numba/actions/runs/39452429887); [Arm CPython 3.13 tests: actual NumPy 2.3.5](https://github.com/numba/numba/actions/runs/39452429967/job/118731667111); [AMD64 CPython 3.13 tests: actual NumPy 2.3.0](https://github.com/numba/numba/actions/runs/39452429887/job/118733985919)

### regex

Confirmed your summary: ONE Windows job builds AMD64, x86 and ARM64. Separate physical jobs are not required. Its bounded job log has explicit per-interpreter Building cpXYZ-win_PLATFORM wheel blocks and successful finished markers, so the monitor now derives independent outcomes for eight matching ARM64/AMD64 interpreter pairs. A failing ARM block is only actionable with its matching completed AMD64 block. An aggregate red icon or incomplete block is insufficient. GitHub annotations explicitly warn that ARM wheels cannot be tested on this AMD64 host, so this channel is build-only, not WoA test coverage.

[Supplied aggregate Windows summary](https://github.com/mrabarnett/mrab-regex/actions/runs/34397217583/attempts/1#summary-102619780387); [Exact aggregate job with independent cibuildwheel interpreter records](https://github.com/mrabarnett/mrab-regex/actions/runs/34397217583/job/102619780387); [Pinned Windows architecture expansion](https://github.com/mrabarnett/mrab-regex/blob/7dd71c15c4fb5c94206bed1763abd4c2bd2f1b33/.github/workflows/main.yml)

### statsmodels

Applied your confirmation: no observable public WoA GitHub build was identified, so statsmodels is excluded/Paused. This is not a regression and not a claim that the package cannot work on Windows Arm64. No missing-builder ping is generated.

[Inspected AMD64-only scheduled wheel workflow](https://github.com/statsmodels/statsmodels/blob/main/.github/workflows/cron-test.yml)

Unknown may mean no new applicable run, not a failed or unconfigured build.
Final collection errors: 0.
Pandas was rechecked for the identical window after raising the measured per-package read cap; no partial scan was presented as complete.
