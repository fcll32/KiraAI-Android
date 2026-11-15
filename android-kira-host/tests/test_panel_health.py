import http.client
import socket
import socketserver
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import panel_health as health


@contextmanager
def endpoint(reply):
    requests = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(1.0)
                lines = [self.rfile.readline(8192)]
                while lines[-1] not in (b'\r\n', b''):
                    lines.append(self.rfile.readline(8192))
                requests.append(b''.join(lines))
                reply(self.request)
            except OSError:
                pass  # The client may close the socket at its deadline.

    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        try:
            yield server.server_address[1], requests
        finally:
            server.shutdown()
            thread.join(1)


class PanelTransportTests(unittest.TestCase):
    def test_component_probes_use_their_official_html_entry_points(self):
        with patch.object(health, 'request', return_value=(200, b'<html>')) as request:
            self.assertTrue(health.probe(6099))
            self.assertEqual(request.call_args.args, (6099, '/webui/'))
            self.assertTrue(health.probe(5267))
            self.assertEqual(request.call_args.args, (5267, '/'))

    def test_chunked_html_uses_numeric_loopback_without_dns(self):
        response = (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
                    b'3\r\n<HT\r\n3\r\nML>\r\n0\r\nX-Trailer: yes\r\n\r\n')
        with endpoint(lambda stream: stream.sendall(response)) as (port, requests):
            with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS lookup')):
                self.assertTrue(health.probe(port))
            self.assertIn(b'Accept: text/html\r\n', requests[0])

    def test_redirect_does_not_follow_location(self):
        with endpoint(lambda stream: stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\n<html>')) as (target, target_requests):
            reply = (f'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{target}/\r\n'
                     'Content-Length: 0\r\n\r\n').encode('ascii')
            with endpoint(lambda stream: stream.sendall(reply)) as (port, requests):
                self.assertFalse(health.probe(port))
                self.assertEqual(len(requests), 1)
                self.assertEqual(target_requests, [])

    def test_rejects_control_header_bytes_before_opening_a_socket(self):
        cases = (
            {'Authorization': 'Bearer fixture\r\n X-Injected: yes'},
            {b'Authorization': b'Bearer fixture\r\nX-Injected: yes'},
            {'Authorization': 'Bearer fixture\x00'},
            {'Authorization': 'Bearer fixture\x7f'},
            {'X-Injected\r\n Header': 'fixture'},
        )
        with patch.object(socket, 'socket', side_effect=AssertionError('socket opened')):
            for headers in cases:
                with self.subTest(headers=headers), self.assertRaisesRegex(ValueError, 'Invalid local panel HTTP header'):
                    health.request(5267, '/api/version', headers=headers)

        response = b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'
        with endpoint(lambda stream: stream.sendall(response)) as (port, requests):
            result = health.request(port, '/api/version', headers={'Authorization': 'Bearer fixture-token.123'})
            self.assertEqual(result, (200, b'{}'))
            self.assertIn(b'Authorization: Bearer fixture-token.123\r\n', requests[0])

    def test_dripping_headers_and_body_cannot_reset_total_deadline(self):
        def drip(stream, content):
            for octet in content:
                stream.sendall(bytes((octet,)))
                time.sleep(.035)

        header = b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\n'
        for start, slow in ((b'', header), (header, b'<html></html')):
            with self.subTest(start=start):
                def reply(stream):
                    stream.sendall(start)
                    drip(stream, slow)

                with endpoint(reply) as (port, _):
                    began = time.monotonic()
                    self.assertFalse(health.probe(port, deadline=health.deadline_after(.25)))
                    self.assertLess(time.monotonic() - began, .8)

    def test_caller_can_share_budget_across_auth_and_version_requests(self):
        response = b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'

        def reply(stream):
            time.sleep(.14)
            stream.sendall(response)

        with endpoint(reply) as (port, requests):
            deadline = health.deadline_after(.25)
            self.assertEqual(health.request(port, '/login', method='POST', body=b'{}', deadline=deadline),
                             (200, b'{}'))
            with self.assertRaises(TimeoutError):
                health.request(port, '/version', deadline=deadline)
            self.assertTrue(requests[0].startswith(b'POST /login HTTP/1.1\r\n'))
            self.assertTrue(requests[1].startswith(b'GET /version HTTP/1.1\r\n'))

    def test_rejects_oversize_and_truncated_bodies(self):
        cases = (
            (b'HTTP/1.1 200 OK\r\nContent-Length: 60000\r\n\r\n', ValueError),
            (b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\n{}', http.client.IncompleteRead),
            (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n11\r\n' + b'x' * 17 + b'\r\n0\r\n\r\n', ValueError),
        )
        for response, error in cases:
            with self.subTest(response=response), endpoint(lambda stream: stream.sendall(response)) as (port, _):
                with self.assertRaises(error):
                    health.request(port, '/', max_bytes=16)

    def test_header_budget_blocks_large_header_aggregation(self):
        response = (b'HTTP/1.1 200 OK\r\n' + (b'X-Example: ' + b'x' * 2048 + b'\r\n') * 24 +
                    b'Content-Length: 6\r\n\r\n<html>')
        with endpoint(lambda stream: stream.sendall(response)) as (port, _):
            self.assertFalse(health.probe(port))


class HealthTrackerTests(unittest.TestCase):
    def test_unconfirmed_observation_expires_without_turning_missing_clock_into_a_fact(self):
        tracker = health.HealthTracker()
        self.assertEqual(tracker.snapshot(60_000)['state'], 'unknown')
        tracker.record(False, 1_000)
        self.assertEqual(tracker.snapshot(30_999)['state'], 'unknown')
        self.assertEqual(tracker.snapshot(31_000)['state'], 'expired')
        self.assertEqual(tracker.snapshot(None)['state'], 'unknown')

    def test_unknown_failure_threshold_recovery_threshold_and_expiry(self):
        tracker = health.HealthTracker()
        self.assertEqual(tracker.snapshot(0)['state'], 'unknown')
        for observed, ok, expected in ((0, False, 'unknown'), (10_000, False, 'unknown'),
                                       (20_000, False, 'unreachable'), (30_000, True, 'unreachable'),
                                       (40_000, True, 'reachable')):
            tracker.record(ok, observed)
            self.assertEqual(tracker.snapshot(observed)['state'], expected)
        self.assertEqual(tracker.snapshot(69_999)['state'], 'reachable')
        self.assertEqual(tracker.snapshot(70_000), {'state': 'expired', 'observed_elapsed_ms': 40_000,
                                                   'last_success_elapsed_ms': 40_000})

    def test_fresh_confirmed_failure_outranks_aged_success(self):
        tracker = health.HealthTracker()
        for observed, ok in ((0, True), (10_000, True), (20_000, False), (30_000, False), (40_000, False)):
            tracker.record(ok, observed)
        self.assertEqual(tracker.snapshot(40_000)['state'], 'unreachable')
        self.assertEqual(tracker.snapshot(70_000)['state'], 'expired')

    def test_failures_do_not_extend_last_success_freshness(self):
        tracker = health.HealthTracker()
        tracker.record(True, 0)
        tracker.record(True, 10_000)
        tracker.record(False, 25_000)
        tracker.record(False, 35_000)
        self.assertEqual(tracker.snapshot(40_000)['state'], 'expired')
        tracker.record(True, 45_000)
        self.assertEqual(tracker.snapshot(45_000)['state'], 'expired')
        tracker.record(True, 55_000)
        self.assertEqual(tracker.snapshot(55_000)['state'], 'reachable')

    def test_stale_and_duplicate_samples_cannot_complete_a_streak(self):
        tracker = health.HealthTracker()
        tracker.record(True, 0)
        tracker.record(True, 0)
        tracker.record(True, 40_000)  # Sleep gap invalidates the earlier streak.
        self.assertEqual(tracker.snapshot(40_000)['state'], 'unknown')
        tracker.record(True, 39_000)
        self.assertEqual(tracker.snapshot(40_000)['state'], 'unknown')
        tracker.record(True, 50_000)
        self.assertEqual(tracker.snapshot(50_000)['state'], 'reachable')
        self.assertEqual(tracker.snapshot(None)['state'], 'unknown')
        self.assertEqual(tracker.snapshot(49_999)['state'], 'unknown')

    def test_missing_boot_clock_returns_unknown_without_synthesizing_timestamp(self):
        with patch.object(health, '_BOOTTIME', None):
            self.assertIsNone(health.boot_elapsed_ms())
        tracker = health.HealthTracker()
        tracker.record(True, None)
        self.assertEqual(tracker.snapshot(None), {'state': 'unknown', 'observed_elapsed_ms': None,
                                                  'last_success_elapsed_ms': None})
