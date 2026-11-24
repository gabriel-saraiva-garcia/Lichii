# Adjacent readback experiment

Status: candidate built; unit tests and a stationary live readback check passed
within their coverage. A/B/B/A observed a small improvement in this scene;
broader correctness and performance coverage remain limited. Native 60 FPS
has not been achieved.
This is an experiment against build-386 PGO, not a demonstrated 60 FPS fix.

## Source and hypothesis

Baseline: `00f580a94b3e368b1502727e98cdd6ad80b000cf`, LLVM 22.1.8,
ThinLTO and the unchanged Windows PGO profile. Candidate `642c04d39` ports
[Lichii #2483](https://github.com/zortos293/Lichii/commit/3f77c820c8e9a217ef8defaf2dc7828e389d6e08)
with attribution. The upstream survey found the Eglantine and pinned Lichii
histories diverged; importing the entire upstream branch would confound this
experiment. Eglantine's analogous read coalescing and earlier-writer wait changes
were reviewed; this candidate retains the newer Lichii ownership guards.

The measured Zora River workload has about 3.3 ms/frame in guest readbacks in a
separate, instrumented run. Adjacent GPU-written pages may otherwise cause
separate waits. Coalescing up to 256 KiB can reduce those waits. The expected
direction is lower readback latency and fewer host waits; the size is unknown.
These costs overlap GPU work, so 3.3 ms is not a promised frame-time saving.
The clean reference median is 43.209 FPS / 23.143 ms, about 6.477 ms above the
60 FPS budget. See `zen3-turing-results-2026-11-24.md` for limitations.

The optimization stops at CPU-dirty, already pending, overlapping, preflushable
or disallowed pages. It snapshots staging data, keeps speculative pages out of
required-read coverage and rechecks ownership at landing. Vulkan completion,
query visibility and CPU writes retain their guards. OpenGL is unchanged.

## Correctness gate

The existing `EDEN_NX2_READ_CHECK` only tracked landed *early* downloads. It
could miss an adjacent speculative page read after that download had landed.
The diagnostic extension records surviving speculative ranges as well,
intersects them with actual write ownership and excludes rewritten reports.
This extension is inactive when read checks are off.

Regression coverage must exercise exact compared-byte counts, an intentionally
unreported GPU overwrite that the checker detects, disabled diagnostics, and
CPU/GPU writes before or after landing. Run the focused cases and then the
complete buffer-port suite from the candidate build:

```powershell
cmake -B $build -DLICHII_TESTS=ON -DBUILD_TESTING=ON
cmake --build $build --target tests --parallel 4
& "$build/bin/tests.exe" '[buffer_ports][read_runs]'
& "$build/bin/tests.exe" '[buffer_ports]'
```

On 2026-11-24, candidate `221fb25c862997db1cd33b36b8932a226d56c2d5`
built with the baseline toolchain/profile. The focused suite passed 9 cases /
424 assertions; the complete buffer-port suite passed 81 cases / 26,519
assertions. The runtime package was preserved before enabling test targets.
Logs and test-executable hashes remain local under the toolchain directory.

Use a separate live diagnostic launch with `EDEN_NX2_READ_CHECK=1` in the saved
Zora River scene. Retain comparison, uncovered, rewritten and mismatch counts;
zero mismatches without meaningful byte coverage is insufficient. Exercise
normal loading and a little movement separately from the stationary benchmark.
Retain private screenshots and logs. Unit tests and a quiet log do not establish
all rendering or synchronization correctness.

The first live diagnostic loaded the identified manual save and retained the
same camera. A cumulative launch snapshot recorded 318,198 checks / 3,820,947
KiB compared, with zero mismatched and zero uncovered GPU-written bytes.
440,747 bytes were excluded after newer writes. This includes loading and
stationary play, not movement or a census of all emulation behavior. The
screenshot was visually consistent with the baseline. Its separate 30-second
instrumented recording measured 31.58 FPS; that is diagnostic overhead and is
excluded from performance acceptance. Raw evidence stays local.

## Performance gate

1. Build and preserve the exact local baseline executable, PDB, deployed Qt
   runtime, manifest and package hashes before reusing its build directory.
   Verify it reproduces the published package's scene and approximate result.
2. Build the candidate with the same toolchain, profile and flags. Preserve its
   package separately. Copy the same published `nx2_switches.txt` to both.
3. Close the build and all captures before measuring. Retain the existing game
   settings, add-ons, shader caches, driver and power settings. Launch with only
   `EDEN_NX2_DEBUG_SERVER=127.0.0.1:6545`; remove diagnostic flags entirely.
4. Load the latest manual Zora River save, leave camera and character untouched,
   confirm the waterfall/platform scene, warm for at least 30 seconds, then
   record three 60-second windows with `tools/nx2/perf_bench.py record`.
   Supply the full source revision, actual executable, both config files and
   package switch file. Follow the command in `zen3-turing.md`.
5. Repeat in A/B/B/A order. Compare with `perf_bench.py compare`; investigate
   fingerprint changes and rerun matched conditions. Report median frame time,
   FPS, tail latency and run spread. Do not infer a gain from an isolated run.
6. Keep the runtime optimization only if the repeated gain exceeds run variation
   and no correctness regression appears. Otherwise retain the evidence and
   move on without merging the candidate into the baseline branch.

The current NVIDIA counter policy blocks standard-user GPU hardware counters.
It has not been weakened. WPR CPU/GPU capture, emulator timestamps, stack samples
and RenderDoc resource inspection provide complementary evidence; capture FPS
is excluded from acceptance. `EDEN_NX2_GPU_PROFILE`, even set to `0`, changes
Maxwell3D paths and must be absent during clean runs.

## Measured result, 2026-11-24

Each fresh launch loaded the same Zora River manual save directly, with no
character/camera movement, a 30-second warm-up and three 60-second windows.
All configuration/switch fingerprints matched. All 25 packaged runtime DLLs
matched between builds; nine shared Streamline DLL hashes remained unchanged.

| Launch order | Build | Median FPS | Median mean frame time | Worst window p99 |
| --- | --- | ---: | ---: | ---: |
| A2 | Exact local build-386 PGO | 46.968 | 21.291 ms | 23.493 ms |
| B1 | Readback candidate | 47.735 | 20.949 ms | 23.139 ms |
| B2 | Candidate after restart | 47.611 | 21.003 ms | 23.012 ms |
| A3 | Baseline after restart | 47.135 | 21.216 ms | 23.555 ms |

Across six windows per arm, median FPS was 47.052 versus 47.669 (+1.313%);
median mean frame time was 21.253 versus 20.978 ms (0.275 ms saved).
Worst-window p99 was 23.555 versus 23.139 ms. These are descriptive run
statistics, not a significance claim or a pooled p99. Raw phase timestamps
are retained; the return-to-baseline phase followed an extended title-menu
interval, then reloaded the save and rewarmed. The remaining median gap to
16.667 ms is about 4.31 ms. Do not credit the earlier published ~43 FPS to this
patch; that is a separate baseline-condition question.

The original published build-386 PGO executable was then retested with the same
save, configuration and switch fingerprints: 47.522, 47.291 and 47.263 FPS
(median 47.291 FPS, mean 21.146 ms, worst-window p99 23.145 ms). It too runs
above the earlier ~43 FPS reference. That historical difference therefore
cannot be credited to rebuilding or this source patch; the precise cause of
the changed conditions remains unresolved. The current published result also
overlaps the candidate's range. Keep the candidate as an experiment, with the
local-build A/B/B/A result distinguished from an end-user release advantage.

Existing unconditional readback counters were also sampled during separate
30-second clean intervals after B2 and A3. A local probe verified the package
executable/PDB hashes, symbol type/field offsets and process image, then read
only the 200-byte counter object through Windows `ReadProcessMemory`, without
writes, injection, privilege changes or enabling profiling flags. Symbol/hash
processing occurred before each interval; snapshots are nontransactional.

| Counter per present | Baseline | Candidate |
| --- | ---: | ---: |
| New async read requests | 0.437 | 0.431 |
| In-flight reads | 5.608 | 5.748 |
| Host read waits | 6.045 | 6.179 |
| Summed host read-wait time | 23.891 ms | 23.129 ms |

This sample **does not establish fewer host waits** in the real scene. Wait
durations were lower while the wait count was slightly higher; the proposed
mechanism is only partly supported. Sums across reading threads overlap and
must not be subtracted from frame time. Profile-gated counters were excluded
from interpretation. The small throughput difference merits further repeats
and scene coverage, not a general 60 FPS or accuracy claim. Preserve this as
a measured candidate while investigating larger remaining costs.
