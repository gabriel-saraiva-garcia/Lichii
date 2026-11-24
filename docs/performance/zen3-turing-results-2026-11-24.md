# Initial Zora River measurements, 2026-11-24

These measurements establish the starting point for the fork. They do not
measure an optimization. See [the workflow](zen3-turing.md) for reproduction,
source architecture, correctness gates and profiling caveats.

## Controlled baseline

Running executable: official-package Windows x86-64 PGO build-386, source
`00f580a94b3e368b1502727e98cdd6ad80b000cf`, Clang 22.1.8. Local executable
SHA-256: `203a35bf8c19a733392b3fd3c9123a004be746f94804f012c9b0ab85ceb8ee8d`.
The existing game installation was used; no new emulator binary was compiled.

Hardware: Ryzen 7 5700X (8 cores/16 logical processors), RTX 2080 Ti
(11264 MiB reported), 17085939712 bytes physical RAM, NVIDIA driver 617.14.
DDR4 3466 MHz is user reported. Windows 11; desktop 2560×1440,
164 Hz reported by Win32_VideoController. Ultimate Performance power plan;
HwSchMode registry value 1, recorded without changing it. Overlays unchanged.

Workload: title `0400486024DEC000`, game 2.0.0; manual Zora River slot_00,
2026-11-24 16:53 America/Sao_Paulo. Continue → latest manual save → Load,
then no character/camera input. Link stands on the circular platform by the
stone tablet, waterfall on the left. Private save hashes and screenshots were
retained locally. A later “latest save” must not replace this scene.

Effective boot log: Vulkan, Docked, GPU accuracy Low/Fast, multicore on,
CPU clock Normal, 1x, FIFO vsync, 60 Hz game limit, DLSS Quality preset 13,
2560×1440 → 3840×2160 upscale. Frame generation disabled. Render follower
**on**, matching the user's current setting. Max Resolution, Texture Pool x2
and FPS Unlock applied; FPS Unlock's dependent patches retained. No accuracy
or quality setting was changed.

Launch: only `EDEN_NX2_DEBUG_SERVER=127.0.0.1:6545` added. Existing package
switches `EDEN_NX2_SPLIT=2` and `EDEN_NX2_FENCE_BATCH=1` retained. User switch
file absent. GPU profile, DKB trace, stack sampling and guest statistics absent
from acceptance windows. The desktop/game remained visible and unpaused.

| Collection | Run 1 FPS | Run 2 FPS | Run 3 FPS | Median FPS | Median mean ms | Worst run p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| Initial, 30 s warmup + 3×60 s | 42.92 | 43.40 | 43.40 | 43.40 | 23.044 | 26.701 |
| Verified collector, already warm, 3×60 s | 42.76 | 43.57 | 43.21 | 43.21 | 23.143 | 28.119 |

The second collection additionally bound the listener to the actual executable
process and checked source revision, sample wall time, interval coverage and
unchanged config/switch hashes. Raw results are local ignored artifacts:
`build/performance-pgo/baseline-zora-1x.json` and
`build/performance-pgo/baseline-zora-1x-verified.json`.

The scene reproduces the user's ~43 FPS observation. At the second collection's
median, reaching a 16.667 ms budget requires about **6.477 ms/frame** less
critical-path time (~28% reduction, ~39% throughput increase). No optimization
or 60 FPS result is claimed. These six windows are same-scene repeatability
evidence, not proof of performance across the game.

## Separate stack diagnostic

After acceptance collection, the debug server sampled `GPU,GPUBack,GPURender,
VulkanWorker,CPUCore_*` for 10 seconds. Ten threads matched, 6447 samples per
thread. Matching PGO symbols were downloaded from release workflow run
`45022962439`, artifact
`symbols-windows-x86_64-pgo-00f580a94b3e368b1502727e98cdd6ad80b000cf`,
and `lichii.pdb` placed beside the running executable. Emulator names resolved.
Raw reports remain local at `build/performance-pgo/samples-1795551208-001.txt`
and its `.folded` companion.

The sampler pauses each target thread and includes blocked stacks. Its
percentages are **sample occupancy, not CPU utilization or milliseconds saved**.
This run is not an acceptance FPS result.

| Thread | Selected self samples | Interpretation / next evidence |
|---|---|---|
| VulkanWorker | 77.8% `ZwWaitForAlertByThreadId`, caller `RtlSleepConditionVariableSRW` | Much observed worker time is waiting; correlate command supply and GPU queue gaps before claiming submission is the bottleneck. |
| GPU front | 38.3% `ZwWaitForMultipleObjects`, 15.3% `ZwWaitForAlertByThreadId`, 7.3% `GpuSplit::Drain`, 4.8% `WaitForSyncedWriters` | Front/back and guest/host dependencies deserve targeted wait attribution. |
| GPUBack | 53.4% `ZwWaitForAlertByThreadId`, 9.6% `GpuSplit::LendRenderCall` | The follower creates visible synchronization; prove any critical-path saving with a separate controlled experiment. |
| GPURender | 50.7% `ZwWaitForAlertByThreadId`; additional time in render-loop code | Waiting and short work bursts can explain moderate average utilization. ETW is needed to separate ready from blocked time. |
| CPUCore_0 | 25.5% JIT, 6.7% `GetCNTPCT`, 6.0% `GetClockTicks`, substantial wait stacks | Guest polling/counter access and read-backs warrant guest-thread statistics. Do not change guest timing semantics to reduce these samples. |

Other guest cores showed JIT plus considerable wait occupancy. Native driver
private symbols are unavailable: labels such as `vkGetInstanceProcAddr` can be
nearest exported symbols for private driver addresses. They must **not** be
interpreted as literal API call hotspots. This capture cannot measure Vulkan
barrier GPU cost or determine whether the GPU stays busy during host waits.

## Separate GPU and guest-wait diagnostic

A second launch added `EDEN_DKB_FRAME_TRACE=1`,
`EDEN_NX2_GUEST_THREAD_STATS=1`, `EDEN_NX2_ACQUIRE_STATS=1` and
`EDEN_NX2_BACK_IDLE_TRACE=1`. The full GPU profile and per-command GPU-site
timestamps remained absent. The same save/settings were loaded without
movement, followed by 30 seconds warmup and one 30-second diagnostic window.

This window measured 41.54 FPS, mean 24.071 ms, p99 27.021 ms. It is **not**
an acceptance comparison: DKB tracing changes some follower recording paths,
and counters/timestamps add overhead. Raw result, final session log and the
analysis are retained under ignored `build/performance-pgo/` as
`diagnostic-zora-1x.json`, `diagnostic-session-log.txt` and
`diagnostic-analysis.json`. Approximately log seconds 160.31–190.33 correspond
to the measured window (debug-server startup offset included).

| GPU timestamp metric | Minimum | Median | Maximum |
|---|---:|---:|---:|
| Submission-interval union, `gpu_busy_ms` (29 sampled frames) | 19.025 ms | 20.670 ms | 22.992 ms |
| First-start to last-end GPU frame span | 21.577 ms | 24.291 ms | 25.951 ms |
| Measured submissions per sampled frame | 27 | 29 | 33 |
| Dropped submissions | 0 | 0 | 0 |

The union excludes gaps **between** submissions; intervals can still contain
GPU synchronization and memory stalls. It is not a hardware counter for useful
shader execution. The high cost means a CPU-only speedup cannot be assumed to
reach 60. Conversely, host waits and gaps mean a GPU-only diagnosis is also
premature. Do not add these times to CPU waits: concurrent work overlaps.

The largest repeated timestamp range was a 2560×1440 pass/range containing
36 draws and 3 dispatches: 4.383–6.585 ms, median 4.447 ms, in 29 samples.
It lists graphics pair `f45e32247832c053` / `fc8435529cbaf776` and compute
hashes `a107e12b6771be48`, `d9ee412f00ce399a`, `a2fcaa95c5a162f3`.
Eight uses are untracked by the bounded hash listing. This is a range to inspect
in RenderDoc/Nsight, **not** isolated timing of any one shader or proof that the
draws are redundant. Several additional compute/copy ranges were visible.

Three 10-second guest reports within this part of gameplay showed thread t88:
about 10.95–11.12 ms/frame on core, including 4.26–4.36 ms counter polling and
4.63–4.76 ms cached-memory handling, of which 3.25–3.36 ms was read-back.
Ready time was only about 0.26–0.27 ms/frame, with about 12.68–12.96 ms waiting.
Polling may be a consequence of waiting on GPU progress; eliminating polling
or changing counter semantics is not a justified fix.

Back-idle reports attributed about 8.32–8.53 ms/frame to the front being in
semaphore handling and about 1.15–1.19 ms/frame to list copies. Front acquire
and list-writer waits were also frequent. Report windows overlap, so their
totals cannot be summed or subtracted from frame time as guaranteed savings.

The next investigations, in order, are:

1. Capture the costly 36-draw range and adjacent compute/copy work with a GPU
   profiler. Measure shader/ROP/bandwidth pressure and synchronization bubbles
   at the exact resource dependencies; preserve all output and image quality.
2. Correlate t88's read-backs, GPU-front acquires and back/worker supply with
   that timeline. Establish which waits are required and which repeated host
   work can be avoided without moving guest visibility or completion.
3. Measure submit/driver duration and barrier/pass-boundary costs before
   changing the existing batching policy. A count of ~29 submits/frame alone
   does not establish that submission overhead is the critical path.
4. Once a local PGO baseline compiles and matches the package's behavior, test
   Zen 3 host targeting as a separate candidate. It cannot directly remove
   guest shader cost or justify altered floating-point/timing semantics.

No runtime optimization was committed. The emulator was subsequently relaunched
with only the debug server, removing diagnostic flags and retaining graphics
settings. Matching symbols remain beside its executable for future profiling.

## Verification and current limits

- Benchmark validity tests: 11 passed; existing debug adapter tests: 17 passed.
- PowerShell Plan output and missing-tool preflight exercised; invalid Test
  invocation rejects before configuring. Whitespace checks passed.
- No emulator C++ build or C++ tests ran: development CMake/Ninja/LLVM/Qt/VS SDK
  are not installed/discoverable. The source/PGO recipe is documented and
  scripted, but successful compilation is still unverified.
- WPR start failed with policy error `0xc5585011` (“Failed to enable the policy
  to profile system performance”). Terminal is not elevated; `wpr -status`
  confirmed no recording remained. There is no ETW capture from this round.
- No RenderDoc/Nsight capture, Vulkan validation run, or renderer optimization
  has yet been validated. A quiet log is not proof of synchronization accuracy.
