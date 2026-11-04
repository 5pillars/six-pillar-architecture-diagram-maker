import json
import os
import shutil
import subprocess
import threading
import time

# Error text that indicates a temporary condition worth retrying.
TRANSIENT = ('Throttl', 'Rate exceeded', 'RequestLimitExceeded', 'TooManyRequests', 'ServiceUnavailable',
             'InternalError', 'Could not connect to the endpoint URL: "https://iam', 'Read timeout', 'Connection was closed')


class AwsError(RuntimeError):
    pass


DEFAULT_MAX_RATE = 3.0   # AWS CLI commands started per second, across all worker threads


class AWS:
    def __init__(self, profile, region, executable=None, expected_account=None, max_rate=DEFAULT_MAX_RATE):
        self.profile = profile
        self.region = region
        self.executable = executable or shutil.which('aws') or 'aws'
        self.expected_account = expected_account
        self.calls = 0
        self.lock = threading.Lock()
        # Commands are started at least 1/max_rate seconds apart, so the scan never goes above max_rate per second
        # however many workers run. 0 or None turns the limit off.
        self.interval = 1.0 / max_rate if max_rate else 0.0
        self.next_start = 0.0
        self.rate_lock = threading.Lock()

    def wait_turn(self):
        if not self.interval:
            return
        # Workers queue on the lock; each start is spaced from the previous actual start, so the clock's
        # resolution can never let a fourth command into the same second.
        with self.rate_lock:
            while True:
                now = time.monotonic()
                if now >= self.next_start:
                    break
                time.sleep(self.next_start - now)
            self.next_start = now + self.interval

    def call(self, service, operation, *arguments, region=None, query=None, timeout=60):
        # No profile: the AWS CLI uses whatever credentials are signed in (environment, default profile, SSO).
        command = [self.executable, service, operation, *map(str, arguments),
                   *(['--profile', self.profile] if self.profile else []), '--region', region or self.region,
                   '--no-cli-pager', '--output', 'json', '--cli-connect-timeout', '8',
                   '--cli-read-timeout', '25']
        if query:
            command += ['--query', query]
        environment = dict(os.environ, AWS_PAGER='', AWS_MAX_ATTEMPTS='5', AWS_RETRY_MODE='adaptive',
                           PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        message = 'AWS CLI failed'
        # Parallel reads can be throttled (IAM especially); transient failures are retried with backoff.
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** attempt)
            try:
                self.wait_turn()   # every attempt is a CLI command, so retries count towards the limit too
                with self.lock:
                    self.calls += 1
                result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                        errors='replace', timeout=timeout, env=environment,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired as exc:
                message = str(exc)
                continue
            except OSError as exc:
                raise AwsError(str(exc)) from exc
            if not result.returncode:
                try:
                    return json.loads(result.stdout) if result.stdout.strip() else None
                except json.JSONDecodeError as exc:
                    raise AwsError('AWS CLI returned invalid JSON') from exc
            message = result.stderr.strip() or f'AWS CLI failed (exit {result.returncode}, no error text)'
            if not (not result.stderr.strip() or any(k in message for k in TRANSIENT)):
                break
        raise AwsError(message[:1600])

    def identity(self):
        value = self.call('sts', 'get-caller-identity')
        if self.expected_account and value['Account'] != self.expected_account:
            raise AwsError('Account identity mismatch; scan stopped before inventory collection.')
        return value

    def login(self):
        # Login is the only non-inventory operation; the user completes browser/MFA.
        result = subprocess.run([self.executable, 'login', '--profile', self.profile],
                                capture_output=True, text=True, timeout=300,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise AwsError('Browser login did not complete. Run aws login --profile ' + self.profile + ' in a terminal.')
        return self.identity()

