#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Protect benchmark validity: bad captures must never become accepted speedups."""
import argparse
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perf_bench  # noqa: E402


TITLE = '0400486024DEC000'
SAMPLE = {'frames': 2400, 'mean_ms': 25.0, 'fps': 40.0, 'p50_ms': 25.0,
          'p90_ms': 27.0, 'p99_ms': 30.0, 'min_ms': 20.0, 'max_ms': 50.0,
          'intervals_ms': [50.0], 'truncated': False}


class FakeEmulator:
    def __init__(self):
        self.samples = 0
        self.closed = False
        self.sample = copy.deepcopy(SAMPLE)
        self.restart = False
        self.elapsed = 0.0

    def call(self, method, params, timeout):
        if method == 'frame_times':
            self.samples += 1
            self.elapsed += 60
            return self.sample
        return {'program_id': TITLE, 'powered_on': True, 'paused': False,
                'renderer': 'vulkan', 'rejected': [], 'scm_rev': 'a' * 40,
                'uptime_s': (10 if self.restart and self.samples else 100 + self.samples * 60),
                'presents': 100 + self.samples * 2400}

    def close(self):
        self.closed = True


class BenchTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.exe = self.root / 'lichii.exe'
        self.exe.write_bytes(b'fake exe')
        self.config = self.root / 'config.ini'
        self.config.write_text('[Renderer]\nframe_gen=false\n')
        self.workload = self.root / 'workload.json'
        self.workload.write_text(json.dumps({'title_id': TITLE, 'game_version': '2.0.0',
                                            'scene': 'fixed save', 'settings': {'frame_generation': False},
                                            'hardware': {'gpu': 'Turing'}}))
        self.args = argparse.Namespace(workload=self.workload, exe=self.exe, config=[self.config],
                                       switches=[], out=self.root / 'run.json', seconds=60,
                                       warmup=0, repeats=3, refresh_hz=60, note='',
                                       launch_environment='debug server only', kind='acceptance',
                                       server='127.0.0.1:6545')
        self.args.expected_revision = 'a' * 40

    def run_fake(self, client=None):
        client = client or FakeEmulator()
        return perf_bench.record(self.args, client, sleep=lambda _: None, clock=lambda: client.elapsed,
                                 identify=lambda *_: {'pid': 1})

    def test_uses_full_window_metrics_not_tail(self):
        result = self.run_fake()
        self.assertTrue(result['complete'])
        self.assertEqual(result['summary']['fps_median'], 40)
        self.assertEqual(result['summary']['p99_ms_worst_run'], 30)
        self.assertEqual(result['samples'][0]['intervals_ms'], [50])

    def test_truncated_capture_stays_incomplete(self):
        client = FakeEmulator()
        client.sample['truncated'] = True
        with self.assertRaises(perf_bench.BenchError):
            self.run_fake(client)
        result = perf_bench.read_json(self.args.out)
        self.assertFalse(result['complete'])
        self.assertIn('truncated', result['error'])
        self.assertTrue(client.closed)

    def test_game_restart_invalidates_run(self):
        client = FakeEmulator()
        client.restart = True
        with self.assertRaisesRegex(perf_bench.BenchError, 'restarted'):
            self.run_fake(client)
        self.assertFalse(perf_bench.read_json(self.args.out)['complete'])

    def test_changed_config_invalidates_capture(self):
        def change_during_warmup(_):
            self.config.write_text('[Renderer]\nframe_gen=true\n')
        with self.assertRaisesRegex(perf_bench.BenchError, 'changed'):
            client = FakeEmulator()
            perf_bench.record(self.args, client, sleep=change_during_warmup,
                              clock=lambda: client.elapsed, identify=lambda *_: {'pid': 1})
        self.assertFalse(perf_bench.read_json(self.args.out)['complete'])

    def test_output_never_overwrites_previous_result(self):
        self.args.out.write_text('previous benchmark')
        with self.assertRaises(FileExistsError):
            self.run_fake()
        self.assertEqual(self.args.out.read_text(), 'previous benchmark')

    def test_rejects_nonfinite_and_oversized_windows(self):
        for seconds in (float('nan'), float('inf'), 61, 0):
            self.args.seconds = seconds
            with self.assertRaises(perf_bench.BenchError):
                self.run_fake()

    def test_rejects_generated_frames(self):
        workload = perf_bench.read_json(self.workload)
        workload['settings']['frame_generation'] = True
        self.workload.write_text(json.dumps(workload))
        with self.assertRaises(perf_bench.BenchError):
            self.run_fake()

    def test_short_burst_after_long_pause_is_not_60fps(self):
        client = FakeEmulator()
        client.sample.update(frames=2, mean_ms=16.667, fps=60.0)
        with self.assertRaisesRegex(perf_bench.BenchError, 'cover'):
            self.run_fake(client)
        self.assertFalse(perf_bench.read_json(self.args.out)['complete'])

    def test_wrong_revision_refused(self):
        self.args.expected_revision = 'b' * 40
        with self.assertRaisesRegex(perf_bench.BenchError, 'revision'):
            self.run_fake()

    def test_compare_allows_different_executables(self):
        left = self.run_fake()
        right = copy.deepcopy(left)
        right['files_sha256'][right['exe']] = 'b' * 64
        right['samples'][0]['fps'] = 50
        right['samples'][1]['fps'] = 50
        right['samples'][2]['fps'] = 50
        self.assertAlmostEqual(perf_bench.compare(left, right)['fps_change_percent'], 25)

    def test_compare_refuses_diagnostics_mismatched_workload_and_config(self):
        left = self.run_fake()
        for mutation in ('kind', 'settings', 'config', 'complete'):
            right = copy.deepcopy(left)
            if mutation == 'kind':
                right['kind'] = 'diagnostic'
            elif mutation == 'settings':
                right['workload']['settings']['scale'] = 0.5
            elif mutation == 'config':
                right['files_sha256'][right['config_files'][0]] = 'b' * 64
            else:
                right['complete'] = False
            with self.subTest(mutation=mutation), self.assertRaises(perf_bench.BenchError):
                perf_bench.compare(left, right)


if __name__ == '__main__':
    unittest.main()
