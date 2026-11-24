# Adjacent readback experiment

Status: candidate prepared; build, correctness and performance acceptance pending.
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
& "$build/bin/tests.exe" '[buffer_ports][read_runs]'
& "$build/bin/tests.exe" '[buffer_ports]'
```

Use a separate live diagnostic launch with `EDEN_NX2_READ_CHECK=1` in the saved
Zora River scene. Retain comparison, uncovered, rewritten and mismatch counts;
zero mismatches without meaningful byte coverage is insufficient. Exercise
normal loading and a little movement separately from the stationary benchmark.
Retain private screenshots and logs. Unit tests and a quiet log do not establish
all rendering or synchronization correctness.

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
