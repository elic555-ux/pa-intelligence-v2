"""Offline session tests with real HTML adapter, fake clock and local Git remotes."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import howardhanna_listing_completion as session
import howardhanna_listing_pilot as worker
import test_howardhanna_listing_pilot as fixtures
from test_howardhanna_listing_pilot import directory, make_rows


class CompletionTests(unittest.TestCase):
    setUp = fixtures.Tests.setUp
    put_inventory = fixtures.Tests.put_inventory
    reader = fixtures.Tests.reader

    def test_one_launch_drains_multiple_groups_and_saves_before_next_group(self):
        reader = self.reader(max_requests=40)
        saved = []
        def checkpoint(repo, signature):
            report = worker.registry.read_json(repo / session.SESSION)
            saved.append((report['groups_completed'], report['new_snapshots'], reader.requests))
            self.assertEqual(signature, hashlib.sha256((repo / 'properties.json').read_bytes()).hexdigest())
        report = session.run(self.repo, reader, self.now, checkpoint)
        self.assertEqual(report['status'], 'cache_current')
        self.assertEqual(report['groups_completed'], 3)
        self.assertEqual(report['new_snapshots'], 7)
        self.assertEqual(report['new_source_network_requests'], 9)
        self.assertEqual([n for _, n, _ in saved], [3, 6, 7, 7])
        self.assertEqual(report['checkpoints_saved'], len(saved))
        self.assertEqual(worker.registry.read_json(self.repo / session.SESSION)['checkpoints_saved'], len(saved))
        self.assertEqual(sum(url.endswith('/robots.txt') for url, _ in reader.calls), 1)
        self.assertTrue(all(b[1] - a[1] >= 10 for a, b in zip(reader.calls, reader.calls[1:])))
        for path, original in self.protected.items():
            self.assertEqual((self.repo / path).read_bytes(), original)

    def test_no_matches_in_first_group_does_not_end_the_directory(self):
        other = [{**self.rows[0], 'id': 'PA-MLS-8888', 'docket_id': 'MLS-8888'}]
        pages = {i: directory(other if i < 4 else self.rows[:1], i, 4) for i in range(1, 5)}
        report = session.run(self.repo, self.reader(pages, max_requests=40), self.now)
        self.assertEqual(report['status'], 'directory_pass_complete')
        self.assertEqual(report['groups_completed'], 2)
        self.assertEqual(report['new_snapshots'], 1)
        self.assertEqual(report['pending'], 6)
        self.assertEqual(report['next_discovery_page'], 1)
        self.assertEqual(report['directory_pages'], 4)

    def test_resume_uses_existing_cursor_and_fresh_cache(self):
        pages = {1: directory(self.rows[:3], 1, 2), 2: directory(self.rows[3:], 2, 2)}
        worker.run(self.repo, 'pilot', self.reader(pages), self.now)
        reader = self.reader(pages, max_requests=40)
        report = session.run(self.repo, reader, self.now)
        self.assertEqual(report['cached_properties'], 3)
        self.assertEqual(report['new_snapshots'], 4)
        self.assertEqual(report['directory_pages'], 1)
        self.assertNotIn(worker.DIRECTORY, [url for url, _ in reader.calls])

    def test_global_request_limit_is_not_reset_between_groups(self):
        reader = self.reader(max_requests=6)
        report = session.run(self.repo, reader, self.now)
        self.assertEqual(report['status'], 'source_request_budget_exhausted')
        self.assertEqual(reader.requests, 6)
        self.assertEqual(report['new_snapshots'], 4)
        next_report = session.run(self.repo, self.reader(max_requests=40), self.now)
        self.assertEqual(next_report['new_snapshots'], 3)
        self.assertEqual(next_report['directory_pages'], 0)

    def test_global_deadline_is_not_reset_between_groups(self):
        reader = self.reader(max_requests=40)
        reader.deadline = 70
        report = session.run(self.repo, reader, self.now)
        self.assertEqual(report['status'], 'batch_time_limit')
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(reader.requests, 5)

    def test_source_block_stops_without_retrying_and_saves_receipt(self):
        reader = self.reader(blocked='detail', max_requests=40)
        saved = []
        report = session.run(self.repo, reader, self.now, lambda *args: saved.append(1))
        self.assertEqual(report['status'], 'source_blocked')
        self.assertEqual(report['groups_completed'], 1)
        self.assertEqual(reader.requests, 3)
        self.assertTrue(saved)
        next_reader = self.reader(max_requests=40)
        next_report = session.run(self.repo, next_reader, self.now)
        self.assertEqual(next_report['status'], 'source_cooldown')
        self.assertEqual(next_reader.requests, 0)

    def test_failed_checkpoint_stops_before_next_group(self):
        reader = self.reader(max_requests=40)
        calls = []
        def failure(*args):
            calls.append(1)
            raise RuntimeError('simulated save failure')
        report = session.run(self.repo, reader, self.now, failure)
        self.assertEqual(report['status'], 'checkpoint_failed')
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['checkpoints_saved'], 0)
        self.assertEqual(reader.requests, 5)
        self.assertEqual(len(calls), 1)

    def test_unexpected_error_retains_and_checkpoints_earlier_groups(self):
        original = worker.run
        calls = [0]
        def run(*args, **kwargs):
            if len(args) > 1 and args[1] == 'pilot':
                calls[0] += 1
                if calls[0] == 2:
                    raise ValueError('bad fixture')
            return original(*args, **kwargs)
        saved = []
        with patch.object(session.pilot, 'run', side_effect=run):
            report = session.run(self.repo, self.reader(max_requests=40), self.now, lambda *args: saved.append(1))
        self.assertEqual(report['status'], 'completion_failed')
        self.assertEqual(report['new_snapshots'], 3)
        self.assertEqual(report['groups_completed'], 1)
        self.assertEqual(len(saved), 2)

    def test_group_cap_prevents_one_launch_draining_unbounded_queue(self):
        self.rows = make_rows(34)
        self.put_inventory()
        report = session.run(self.repo, self.reader(max_requests=40), self.now)
        self.assertEqual(report['status'], 'group_limit_reached')
        self.assertEqual(report['groups_completed'], 10)
        self.assertEqual(report['new_snapshots'], 30)
        self.assertEqual(report['pending'], 4)
        self.assertEqual(report['new_source_network_requests'], 32)

    def test_all_fresh_cache_makes_no_network_requests(self):
        session.run(self.repo, self.reader(max_requests=40), self.now)
        reader = self.reader(max_requests=40)
        report = session.run(self.repo, reader, self.now)
        self.assertEqual(report['status'], 'cache_current')
        self.assertEqual(report['new_snapshots'], 0)
        self.assertEqual(reader.requests, 0)

    def test_git_checkpoint_pushes_only_evidence_and_rejects_changed_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / 'remote.git'
            other = Path(tmp) / 'other'
            def git(path, *args):
                return subprocess.run(['git', *args], cwd=path, check=True, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
            git(Path(tmp), 'init', '--bare', str(remote))
            git(self.repo, 'init', '-b', 'main')
            git(self.repo, 'config', 'user.name', 'Offline test')
            git(self.repo, 'config', 'user.email', 'test@example.invalid')
            git(self.repo, 'add', '.')
            git(self.repo, 'commit', '-m', 'fixture')
            git(self.repo, 'remote', 'add', 'origin', str(remote))
            git(self.repo, 'push', '-u', 'origin', 'main')
            report = session.run(self.repo, self.reader(max_requests=40), self.now, session.save_checkpoint)
            self.assertEqual(report['status'], 'cache_current')
            self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), git(remote, 'rev-parse', 'refs/heads/main'))
            changed = git(self.repo, 'diff', '--name-only', 'HEAD~4', 'HEAD').splitlines()
            self.assertTrue(all(p.startswith('COMPS_REPORTS/') for p in changed))
            git(Path(tmp), 'clone', '--branch', 'main', str(remote), str(other))
            git(other, 'config', 'user.name', 'Other')
            git(other, 'config', 'user.email', 'other@example.invalid')
            (other / 'unrelated.txt').write_text('remote changed')
            git(other, 'add', '.')
            git(other, 'commit', '-m', 'other change')
            git(other, 'push', 'origin', 'main')
            with self.assertRaisesRegex(RuntimeError, 'repository_changed'):
                session.save_checkpoint(self.repo, report['properties_sha256'])
            (self.repo / 'properties.json').write_text('[]')
            with self.assertRaisesRegex(RuntimeError, 'inventory_changed'):
                session.save_checkpoint(self.repo, report['properties_sha256'])


if __name__ == '__main__':
    unittest.main()
