# Windows Zen 3 / Turing performance fork

The first milestone is a measured baseline and a reproducible build. No renderer,
emulation, accuracy, clock, or scheduling defaults are changed by this work.
60 native FPS at the same image quality remains a target, not an achieved result.

## Baseline identity and workload

- Source: `build-386`, `00f580a94b3e368b1502727e98cdd6ad80b000cf`.
- Windows PGO: clang-cl **22.1.8**, ThinLTO, repository `lichii-windows.profdata`.
  Profile SHA-256: `07cc193d6d8faa0a9ef94a6c4a3bfa43cf2918516f9a9e430c40e145b6de20bc`.
- Target: Ryzen 7 5700X, RTX 2080 Ti 11 GB, 16 GB DDR4 3466 MHz,
  Windows 11, Vulkan. RAM speed is user reported; record actual driver/OS versions per round.
- Ocarina of Time, title `0400486024DEC000`, update 2.0.0. Built-in patches
  target main build ID `E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348`.
- Initial scene: latest **manual Zora River save, 2026-11-24 16:53
  America/Sao_Paulo**, load and do not move. Default spawn camera faces the
  waterfall from the circular platform. Retain a local screenshot and save-file
  hashes so a newer “latest save” cannot silently replace this workload.
- Keep Max Resolution, Texture Pool x2, FPS Unlock, and all other add-ons fixed.
  Dynamic FPS is superseded when FPS Unlock applies; distinguish listed add-ons
  from patches actually applied in the boot log.

The user's initial results were 44 FPS at 1x, 52 at 0.75x, and 53–55 at 0.5x
(22.73, 19.23, and 18.18–18.87 ms/frame). They suggest resolution-dependent work
and a performance floor, but do not identify the critical path. Thread migration,
bursts, serialization, read-backs, and fixed-size GPU work can all hide behind
moderate CPU/GPU utilization. Do not fit a single pixel-cost model to these three
points or interpret GPU utilization as a direct measure of useful work.

“1x” is insufficient workload identity. `patch_manager.cpp` documents Max
Resolution's fixed 2560×1440 guest path. The active baseline log records DLSS
Quality, 2560×1440 → 3840×2160, followed by presentation on a 2560×1440 desktop.
Record guest render size, upscale size, final display size, docked mode, DLSS
quality/preset, vsync, and display refresh separately. Lower-scale runs are
diagnostic experiments, not acceptance at reduced quality.

## Architecture and profiling entry points

The live path is guest ARM CPU execution (Dynarmic under `src/core/arm` and
`src/dynarmic`) and HLE services → GPU command lists / DMA / MME → GPU front
thread → split back thread → Vulkan scheduler recording worker → host GPU queue
→ composite/present. The optional GPURender follower adds another CPU stage;
it is distinct from the normal Vulkan worker and split back thread.

| Question | Source entry points | Evidence needed before changing behavior |
|---|---|---|
| CPU/GPU and front/back synchronization | `src/video_core/gpu_split.cpp` (`Drain`, `Backoff`), `gpu_split.h` drain reasons; `renderer_vulkan/vk_rasterizer.cpp` (`FlushRegionForRead`, `FinishRead`); `buffer_cache/buffer_cache.h` | Acquire/list-writer/copy waits, read-back bytes and time, back idle reason, guest-thread waits; correlate with a GPU timeline. |
| Fences and forced completion | `renderer_vulkan/vk_scheduler.cpp` (`Finish`, `WaitWorker`); `vk_master_semaphore.cpp` (`Wait`) | Distinguish waiting for the recording worker, a submitted GPU tick, guest fences, and presentation. A fence wait is not proof of continuous GPU occupancy. |
| Queue submission overhead | `vk_scheduler.cpp` (`SubmitExecution`), `vk_master_semaphore.cpp` (`SubmitQueue`), `submit_mutex` | Submits/frame, CPU duration and lock contention, queue depth, GPU idle gaps, work per submission, and pending-work latency. |
| Vulkan barriers and pass breaks | `vk_scheduler.cpp` compute→graphics and upload barriers; `vk_rasterizer.cpp` (`FragmentBarrier`, WFI handling) | GPU trace plus the exact producer/consumer resources, layouts, stages/access and guest ordering. Compare critical-path stall time, not barrier count alone. |
| Redundant rendering/state work | `vk_rasterizer.cpp` fast-draw reporting, draw reuse probe, `FlushCommands`, `FlushWork`; texture and buffer caches | Repeated state cost, pass restarts, copies/resolves, cache misses, and image lifetimes; prove the later operation observes identical contents. |
| Scheduling/contention | `src/core/hle/kernel`, `src/core/core_timing.cpp`, GPU split and Vulkan worker | ETW sampled stacks plus ready/wait durations and wakeups by thread. Distinguish guest main-thread work from ready-but-unscheduled host work. |
| Resolution-independent GPU costs | `src/shader_recompiler`, `renderer_vulkan/vk_compute_pipeline.cpp`, render/compute pass captures | Fixed-size dispatches, mesh/task fallback if active, read-backs, copies and upscale/composite cost at each scale. Inspect actual dimensions, not just settings. |
| Host compiler targeting | root `CMakeLists.txt`, `src/dynarmic`, `lichii-windows.profdata` | Compare identical PGO/ThinLTO builds, then separately test explicit Zen 3 code generation. Host flags do not directly optimize translated guest GPU shaders. |

Empty-submit elimination, fence batching, split front/back processing, deferred
fragment barriers, fast draws, and several Ocarina-specific paths already exist
in this baseline. Measure them before reimplementing an optimization.

## Reproduce the Windows build

Use the baseline's release workflow as the authority:
`.github/workflows/release-binaries.yml` → **Configure Windows**, plus
`tools/windows/install-llvm.ps1` and `docs/CI.md`. Do not compare a plain
Release build against the published PGO executable as if their compiler
configuration were identical.

Required tools:

1. Visual Studio 2022 C++ build tools, MSVC headers/libraries, Windows SDK.
2. CMake ≥3.31 and Ninja, with their exact versions retained in `preflight.json`.
3. LLVM **22.1.8**, including clang-cl, lld-link, llvm-lib and llvm-profdata.
   The existing installer pins SHA-256
   `16e5709785fef73c854646241c4a92c5cd574318d1b33c63330dd7721903e55c`.
   It expects `RUNNER_TEMP` and `GITHUB_ENV`; for local use, set these to a
   toolchain scratch directory and a local env-output file, then set LLVM_ROOT
   to the installed directory. Review the installer before running it.
4. Qt **6.8.3**, `win64_msvc2022_64`, including `qtcharts` and
   `qtimageformats`, as in the release job. Set QT_ROOT_DIR to its kit directory.
5. Python ≥3.10 for the benchmark tools; these use only the standard library.

In a fresh PowerShell terminal with those tools installed:

```powershell
./tools/windows/load-msvc-env.ps1
$env:LLVM_ROOT = 'C:/Toolchains/llvm-22.1.8'
$env:QT_ROOT_DIR = 'C:/Qt/6.8.3/msvc2022_64'
./tools/windows/build-performance.ps1 -Action Plan
./tools/windows/build-performance.ps1 -Action Preflight
./tools/windows/build-performance.ps1 -Action Build -Jobs 4
# Compile and run checks in a separate directory; no emulator launch:
./tools/windows/build-performance.ps1 -Action Test -Tests -BuildDirectory build/performance-tests -Jobs 4
```

The script uses the release compiler/PGO/ThinLTO recipe and optimized Release
with debug information. It retains source revision, dirty status, tool versions,
profile/dependency/compiler hashes, arguments and successful artifact hashes.
It does not advertise a local binary as the official build-386 release.
Compiler caching and the optional ThinLTO disk cache are omitted: these change
build time, not intended runtime behavior. The updater is not packaged here.
Use the release workflow's existing packaging steps if making a distributable;
retain all required runtime DLLs and symbols beside their matching build.

Four compile jobs are a conservative starting point on 16 GB RAM. Record peak
commit/pagefile use if changing it. Do not compile or run shader precompilation
while benchmarking. The preflight fails rather than substituting compilers,
using the Linux PGO profile, or silently falling back to a non-PGO build.
This is reproducibility of source/toolchain/flags, not a bit-for-bit binary claim;
record the MSVC/SDK and transitive dependency selections too.

The build helper also accepts `-SourceDirectory`, so the recipe can compile a
clean detached build-386 worktree while development stays on the fork branch.
Use an absolute `-BuildDirectory` when keeping outputs beside that worktree.
Qt's version is checked with the kit's `qmake -query QT_VERSION`; the outer
Qt6ConfigVersion.cmake may delegate to another file and contain no version.

The owner's local tools are in the sibling `Lichii-toolchain` directory. Its
`Enter-LichiiToolchain.ps1` activates the current shell, `Build-Baseline.ps1`
builds the pinned worktree, and `Verify-Toolchain.ps1` checks installed tools.
Its README and receipts describe the exact installed versions and paths.
Microsoft requires WPT to share the existing Windows SDK root; the local
`windows-adk/WindowsPerformanceToolkit` entry is a junction to that system
installation. No display driver change is required for this setup.

As of the initial local inspection, the target PC lacks discoverable CMake,
Ninja, LLVM, Qt development files and the Visual Studio SDK. The generated
preflight report documents these failures. Compilation is not yet verified.

## Reproduce the warm-scene benchmark

Use the existing PGO package as A until the local baseline build is validated.
Start it yourself with a loopback debug server (or explicitly authorize the
agent to launch/restart it). Do not run two emulator instances against one user
profile. After an ordinary exit:

```powershell
$env:EDEN_NX2_DEBUG_SERVER = '127.0.0.1:6545'
& 'F:/Games/Emuladores/lichii-windows-x86_64-pgo/lichii.exe' -g 'D:/Games/Legend of Zelda - Ocarina of Time/games/0400486024dec000_v131072.nup'
```

Choose Continue → the identified latest manual save → Load. Touch no movement
or camera controls. Let assets/pipelines finish warming. Retain a screenshot
before measurement; take captures outside measured windows. Prepare a local
copy of `tools/nx2/perf_workload.example.json`, replacing every `FILL_` value
with the effective settings, scene and hardware. Keep raw captures/results
under ignored `build/performance-pgo/`, not in public issues or source control.

```powershell
py -3.10 -I tools/nx2/perf_bench.py record `
  --workload build/performance-pgo/workload-zora-1x.json `
  --exe 'F:/Games/Emuladores/lichii-windows-x86_64-pgo/lichii.exe' `
  --expected-revision 00f580a94b3e368b1502727e98cdd6ad80b000cf `
  --config "$env:APPDATA/lichii/config/qt-config.ini" `
  --config "$env:APPDATA/lichii/config/custom/0400486024DEC000.ini" `
  --switches 'F:/Games/Emuladores/lichii-windows-x86_64-pgo/nx2_switches.txt' `
  --kind acceptance --launch-environment 'debug server only; diagnostics unset; package switches unchanged' `
  --warmup 30 --seconds 60 --repeats 3 --refresh-hz 60 `
  --out build/performance-pgo/baseline-zora-1x.json
```

Include the user `nx2_switches.txt` with another `--switches` if one exists, and
all other configuration files actually used. Environment variables override
switch files; user switch files override package files. The launch-environment
argument is an explicit **operator attestation**, not automatic inspection of
the running process's environment. On Windows the collector verifies the TCP
listener's owning process path and creation time, hashes its executable and
cross-checks the required source revision. Do not label an unverified
launch “acceptance.” Config hashes cannot detect settings changed in memory;
keep the UI untouched and verify the effective boot log.

The collector only calls `status` and `frame_times`. It hashes executable,
configuration and switch files before/after, validates title/revision/uptime and
Vulkan state, verifies window coverage and elapsed time, rejects truncated
samples and retains partial failures. It never
launches/stops the game, sends input, or modifies settings/saves/caches.
The server computes its percentiles from the full retained interval window;
`intervals_ms` is a rounded tail of at most 120 intervals. The collector uses
the server's metrics, reports per-run results, median FPS, run spread and
worst-run p99. It does not manufacture a pooled p99 from that tail. Sixty-second
windows fit 4096 retained timestamps at the 60 FPS cap; faster runs may require
shorter windows. `--refresh-hz 60` classifies intervals against the target
16.67 ms cadence; record physical refresh separately (164 Hz in the initial
inventory). These are guest-present intervals, not measured display latency.

For each candidate, alternate A/B/B/A, reload the same save, rewarm identically,
and collect ≥3 windows per build. Hold driver, add-ons, clocks, power plan,
HAGS, window mode, overlays and caches constant. Then compare:

```powershell
py -3.10 -I tools/nx2/perf_bench.py compare build/performance-pgo/baseline-zora-1x.json build/performance-pgo/candidate-zora-1x.json
```

The comparator refuses diagnostic, incomplete, different-workload or different
config/switch results. If GUI bookkeeping changes a whole-file config hash,
inspect the diff and remeasure with matched files; do not bypass it silently.
Compare run spread before attributing small gains. Stable native 60 needs
sustained frame-time headroom near 16.67 ms plus consistent tail latency, not
just an instantaneous FPS counter. Validate animation, audio, loading, scene
transitions, and image output as well; a single stationary scene cannot prove
global stability or emulation correctness.

## Diagnostic passes and interpretation

Keep separate launch manifests/results for each pass. Remove an environment
variable to disable it: some switches use **presence**, so `=0` still enables
them. Inspect both switch files and inherited environment.

| Pass | Launch additions | Purpose and observer effect |
|---|---|---|
| Clean acceptance | Debug server only | No GPU profile, DKB trace, per-command sites, guest stats, draw probe, validation layers, RenderDoc or profiler injection. |
| GPU sample | `EDEN_DKB_FRAME_TRACE=1` | GPU-frame, submission and pass samples (`DKB gpu_frame`, `gpu_pass`). Timestamp/instrumentation costs apply; not acceptance FPS. Leave `EDEN_DKB_GPU_SITES` absent. |
| Guest/split waits | `EDEN_NX2_GUEST_THREAD_STATS=1`, `EDEN_NX2_ACQUIRE_STATS=1`, `EDEN_NX2_BACK_IDLE_TRACE=1` | Guest thread work/ready/wait categories and front/back waiting sites. Compare ms/frame, not only ms/s. |
| Full GPU CPU zones | `EDEN_NX2_GPU_PROFILE=1` | Zones, GPU waits, submit statistics, spin contention and read-backs. **Changes the Maxwell3D fast path**, not just timing cost. |
| Repeated draw state | `EDEN_NX2_DRAW_REUSE_PROBE=1` | Draw reuse gate/cost; preserves fast Maxwell3D. Still instrumentation; alters some experimental follower paths. |

`engines/maxwell_3d.cpp` enables `fast_maxwell3d` only when the GPU profile is
disabled. DKB trace and draw probing also inhibit some render-follower unlend
paths. The initial clean run preserves the user's follower-on setting; later
isolate follower-off as its own documented experiment. Never compare a traced
follower-off result against an untraced follower-on result as a code speedup.

Use WPR/WPA for CPU stacks, thread ready/wait durations and GPU scheduling.
Check `wpr -status` first so another recording is not disturbed; elevation and
Windows Performance Toolkit may be required. On this machine `wpr -profiles`
lists `CPU`, `GPU`, and `GeneralProfile`. A short, separate capture can use:

```powershell
wpr -start GeneralProfile -start GPU -filemode
# Remain at the scene for 30 seconds; no compile/capture work in parallel.
wpr -stop build/performance-pgo/zora-cpu-gpu.etl
```

Always stop a recording you started. Do not cancel another session. In WPA,
locate the emulator PID, align guest/frame activity with GPU queue execution,
and separate running, ready and waiting time for GPU front/back/worker and
guest cores. A lack of PDBs limits stack attribution; a newly built executable
cannot use the release's different symbols. JIT code also needs separate
guest/JIT attribution. Microsoft documents the
[WPR/WPA profiling tools](https://learn.microsoft.com/en-us/windows/apps/develop/performance/profiling-tools).

For detailed Vulkan CPU API / GPU queue correlation use
[Nsight Systems Vulkan and WDDM tracing](https://docs.nvidia.com/nsight-systems/UserGuide/index.html).
For GPU units and queue synchronization use
[Nsight Graphics GPU Trace](https://docs.nvidia.com/nsight-graphics/UserGuide/gpu-trace-overview.html),
which supports Turing and later. Check installed-tool/driver compatibility;
not every modern profiler feature is available on Turing. RenderDoc is useful
for resource dependencies, event ordering and image correctness, not acceptance
timing. Launch/inject only with authorization and keep captures local.

The debug server also offers `sample`, a Windows stack sampler for named host
threads. It suspends them roughly once per millisecond, so use it only in a
separate diagnostic window. For example:

```powershell
py -3.10 -I tools/nx2/debug_mcp.py --server 127.0.0.1:6545 --call '{"method":"sample","params":{"threads":"GPU,GPUBack,GPURender,VulkanWorker,CPUCore_*","seconds":10}}'
```

Check the actual thread names and report's match count; an unmatched pattern
provides no evidence about that thread. Sampling includes blocked stacks and
cannot replace ETW ready/wait attribution. Download matching PGO PDBs from the
baseline release workflow's `symbols-windows-x86_64-pgo-<sha>` artifact if still
retained; place the matching `lichii.pdb` beside the executable before first
sampling or configure a symbol path before launch. DbgHelp must actually resolve
names; unresolved addresses are not function-level evidence.

For barriers, identify producer and consumer accesses using a capture before
narrowing stages/access or eliminating a barrier. Check indirect arguments,
vertex data, storage writes, layouts and host visibility. Follow the
[Vulkan synchronization rules](https://docs.vulkan.org/spec/latest/chapters/synchronization.html).
Do not remove a fence/read-back because its CPU wait looks expensive.

## Incremental optimization contract

Before writing a runtime fix, complete the repository's Eglantine upstream
search and inspect relevant diffs. Keep this pinned baseline intact; any
upstream update must be an explicit, separately measured candidate.

Each optimization gets one commit with: observed critical-path evidence;
expected mechanism and ms/frame opportunity; the exact changed invariant;
baseline/candidate source and executable hashes; build and benchmark commands;
per-run mean/p99, variation and image/gameplay checks; and a revert criterion.
Keep only reproducible gains above run-to-run noise, with no quality reduction
or correctness regression. Do not combine barrier, submission, thread-policy,
and compiler changes in one candidate.

The first compiler experiment, after baseline build validation, can test
explicit Zen 3 targeting (`znver3`) with the same Windows PGO profile and
ThinLTO. Test compiler acceptance/actual generated commands first, and keep it
separate from PGO retraining. Do not use `znver4`, AVX-512, unsafe floating-point
flags, global affinity/priority changes, or disabled validation as substitutes
for profiling. Leave assertions/correctness checks and rendering semantics
unchanged. Host tuning cannot promise the roughly 6.6 ms/frame reduction needed
to turn a 43 FPS scene into stable 60 FPS.
