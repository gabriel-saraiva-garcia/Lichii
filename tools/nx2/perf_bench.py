#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only warm-scene benchmarks against an already running local debug server.

Never launches/stops the emulator, injects input, or changes settings/caches/saves.
Keep diagnostic captures separate from acceptance benchmarks. See docs/performance/zen3-turing.md.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from debug_mcp import Emulator, EmulatorError  # noqa: E402


class BenchError(Exception):
    pass


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def fingerprint(paths):
    return {str(Path(path).resolve()): digest(path) for path in paths}


def validate_workload(workload):
    required = ('title_id', 'game_version', 'scene', 'settings', 'hardware')
    if any(not workload.get(key) for key in required):
        raise BenchError('workload needs title_id, game_version, scene, settings and hardware')
    if not isinstance(workload['settings'], dict) or not isinstance(workload['hardware'], dict):
        raise BenchError('settings and hardware must be JSON objects')
    if 'FILL_' in json.dumps(workload):
        raise BenchError('fill every FILL_ placeholder in the workload first')
    if workload['settings'].get('frame_generation') is not False:
        raise BenchError('native-FPS benchmark requires frame_generation: false')


def validate_status(status, title_id):
    if str(status.get('program_id', '')).upper() != title_id.upper():
        raise BenchError('debug server is running another title')
    if not status.get('powered_on') or status.get('paused'):
        raise BenchError('game must be running and unpaused')
    if status.get('renderer') != 'vulkan' or status.get('rejected'):
        raise BenchError('requires Vulkan with no shaders rejected')
    if not status.get('scm_rev') or not isinstance(status.get('uptime_s'), (int, float)):
        raise BenchError('missing revision or uptime in status')


def validate_sample(sample, seconds):
    if sample.get('truncated') is not False or sample.get('frames', 0) < 2:
        raise BenchError('empty/truncated frame ring; discard run and reduce sample duration')
    for key in ('mean_ms', 'fps', 'p50_ms', 'p90_ms', 'p99_ms', 'min_ms', 'max_ms'):
        value = sample.get(key)
        if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise BenchError(f'invalid frame metric: {key}')
    coverage_s = sample['frames'] * sample['mean_ms'] / 1000.0
    if not seconds - 0.5 <= coverage_s <= seconds + 0.5:
        raise BenchError('frame intervals do not cover the requested window; possible pause/hang')


def server_identity(address, exe):
    """Bind the Windows loopback listener to its real process, without injection."""
    port = int(address.rpartition(':')[2])
    if not 1 <= port <= 65535:
        raise BenchError('invalid server port')
    if os.name != 'nt':
        raise BenchError('process verification currently requires Windows')
    # Only a validated integer enters this fixed PowerShell program.
    script = (f"$ErrorActionPreference='Stop'; Get-NetTCPConnection -LocalPort {port} -State Listen | "
              "Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { "
              "Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $_) } | "
              "Select-Object ProcessId,ExecutablePath,CreationDate | ConvertTo-Json -Compress")
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                               capture_output=True, text=True, check=True, timeout=15)
    identity = json.loads(completed.stdout)
    if not isinstance(identity, dict) or not identity.get('ExecutablePath'):
        raise BenchError('cannot identify a unique debug-server process')
    if os.path.normcase(os.path.realpath(identity['ExecutablePath'])) != os.path.normcase(os.path.realpath(exe)):
        raise BenchError('--exe does not match the process owning the debug-server port')
    return identity


def summarize(samples):
    # The server returns only a rounded tail in intervals_ms. Its own per-window
    # percentiles use the whole window; never pool or recompute from that tail.
    return {
        'runs': len(samples),
        'fps_median': statistics.median(s['fps'] for s in samples),
        'fps_range': [min(s['fps'] for s in samples), max(s['fps'] for s in samples)],
        'mean_ms_median': statistics.median(s['mean_ms'] for s in samples),
        'p99_ms_median_of_runs': statistics.median(s['p99_ms'] for s in samples),
        'p99_ms_worst_run': max(s['p99_ms'] for s in samples),
    }


def record(args, emulator=None, sleep=time.sleep, clock=time.monotonic, identify=server_identity):
    workload = read_json(args.workload)
    validate_workload(workload)
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 60:
        raise BenchError('seconds must be 1..60 (the server retains 4096 timestamps)')
    if not math.isfinite(args.warmup) or not 0 <= args.warmup <= 600:
        raise BenchError('warmup must be 0..600 seconds')
    if not math.isfinite(args.refresh_hz) or args.refresh_hz <= 0 or not 1 <= args.repeats <= 20:
        raise BenchError('refresh_hz must be positive; repeats must be 1..20')
    host, _, _ = args.server.rpartition(':')
    if host not in ('127.0.0.1', 'localhost', '[::1]'):
        raise BenchError('use a loopback debug server')
    files = fingerprint([args.exe, *args.config, *args.switches])
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents a mistyped output name destroying an earlier run.
    with output.open('x', encoding='utf-8') as stream:
        stream.write('{}\n')
    result = {
        'schema': 1, 'complete': False, 'kind': args.kind,
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'workload': workload, 'files_sha256': files,
        'exe': str(Path(args.exe).resolve()),
        'config_files': [str(Path(p).resolve()) for p in args.config],
        'switch_files': [str(Path(p).resolve()) for p in args.switches],
        'seconds_per_run': args.seconds, 'warmup_s': args.warmup, 'refresh_hz': args.refresh_hz,
        'note': args.note, 'samples': [],
        'launch_environment_attestation': args.launch_environment,
        'expected_revision': args.expected_revision,
    }
    client = emulator or Emulator(args.server)
    try:
        identity = identify(args.server, args.exe)
        result['process'] = identity
        before = client.call('status', {'fps_window_ms': 0}, 5)
        validate_status(before, workload['title_id'])
        if before['scm_rev'] != args.expected_revision:
            raise BenchError('running source revision does not match --expected-revision')
        result['before'] = before
        write_json(output, result)
        print(f'Warming scene for {args.warmup:g}s; leave input untouched.', flush=True)
        sleep(args.warmup)
        for number in range(args.repeats):
            start_status = client.call('status', {'fps_window_ms': 0}, 5)
            validate_status(start_status, workload['title_id'])
            started = clock()
            sample = client.call('frame_times', {'seconds': args.seconds,
                                                 'refresh_hz': args.refresh_hz}, args.seconds + 35)
            validate_sample(sample, args.seconds)
            after = client.call('status', {'fps_window_ms': 0}, 5)
            elapsed = clock() - started
            validate_status(after, workload['title_id'])
            delta = after.get('presents', 0) - start_status.get('presents', 0)
            if (after['scm_rev'] != before['scm_rev'] or start_status['scm_rev'] != before['scm_rev'] or
                    abs(after['uptime_s'] - start_status['uptime_s'] - elapsed) > 1.0 or
                    abs(delta - (sample['frames'] + 1)) > max(4, delta * 0.02)):
                raise BenchError('game restarted or stopped presenting during benchmark')
            if not args.seconds - 0.1 <= elapsed <= args.seconds + max(1, args.seconds * 0.05):
                raise BenchError('sample wall time differs from requested duration')
            sample = dict(sample, wall_seconds=elapsed, status_present_delta=delta)
            result['samples'].append(sample)
            result['after'] = after
            write_json(output, result)
            print(f'Run {number + 1}: {sample["fps"]:.2f} FPS; '
                  f'mean {sample["mean_ms"]:.3f} ms; p99 {sample["p99_ms"]:.3f} ms', flush=True)
        if fingerprint(files) != files:
            raise BenchError('executable/config/switch files changed during measurement')
        if identify(args.server, args.exe) != identity:
            raise BenchError('debug-server process changed during measurement')
        result['summary'] = summarize(result['samples'])
        result['complete'] = True
        write_json(output, result)
        return result
    except (OSError, ValueError, subprocess.SubprocessError, EmulatorError, BenchError, KeyboardInterrupt) as error:
        result['error'] = str(error) or type(error).__name__
        write_json(output, result)
        raise
    finally:
        client.close()


def compare(left, right):
    for run in (left, right):
        if not run.get('complete') or run.get('kind') != 'acceptance':
            raise BenchError('compare requires two complete acceptance benchmarks')
    for key in ('workload', 'seconds_per_run', 'refresh_hz', 'launch_environment_attestation'):
        if left[key] != right[key]:
            raise BenchError(f'incomparable {key}; create a separate experiment')
    # Executables differ in A/B. Config and switch content must match, even across package paths.
    for key in ('config_files', 'switch_files'):
        a = sorted(left['files_sha256'][path] for path in left[key])
        b = sorted(right['files_sha256'][path] for path in right[key])
        if a != b:
            raise BenchError(f'incomparable {key} fingerprints')
    a, b = summarize(left['samples']), summarize(right['samples'])
    return {'baseline': a, 'candidate': b,
            'fps_change_percent': (b['fps_median'] / a['fps_median'] - 1) * 100,
            'mean_ms_saved': a['mean_ms_median'] - b['mean_ms_median'],
            'warning': 'Descriptive run statistics, not significance or correctness proof.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('record')
    run.add_argument('--server', default='127.0.0.1:6545')
    run.add_argument('--workload', required=True)
    run.add_argument('--exe', required=True)
    run.add_argument('--expected-revision', required=True, help='full source SHA expected in server status')
    run.add_argument('--config', action='append', required=True)
    run.add_argument('--switches', action='append', default=[])
    run.add_argument('--kind', choices=('acceptance', 'diagnostic'), required=True)
    run.add_argument('--launch-environment', required=True,
                     help='attest actual launch flags, including switch-file diagnostics; not auto-detected')
    run.add_argument('--seconds', type=float, default=60)
    run.add_argument('--warmup', type=float, default=30)
    run.add_argument('--repeats', type=int, default=3)
    run.add_argument('--refresh-hz', type=float, default=60)
    run.add_argument('--note', default='')
    run.add_argument('--out', required=True)
    diff = sub.add_parser('compare')
    diff.add_argument('baseline')
    diff.add_argument('candidate')
    args = parser.parse_args()
    try:
        if args.command == 'record':
            result = record(args)['summary']
        else:
            result = compare(read_json(args.baseline), read_json(args.candidate))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, EmulatorError, BenchError) as error:
        print(f'perf_bench: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
