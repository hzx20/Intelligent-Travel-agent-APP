"""Verify the rehearsed payload; explicitly run one authorized copy check and archive its receipt."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'backend/tools/verify_protected_restore.py'
EVIDENCE = REPO / 'docs/verification/budget-protected-restore-preflight-2026-11-18.json'
ARCHIVE = REPO / '.release-candidates/budget-protected-restore-2026-11-18'
SHA = 'd8b08b183c887b725cc198fe88426c4901bab2bccbf0ca72a172fe30758f5a71'
CANDIDATE = '27592646387039b1e5d167264fa7fc1b31f672e3392167de0cf550dabeab45a6'
SSH = ('ssh', '-T', '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-oConnectTimeout=8',
       '-oClearAllForwardings=yes', '-oForwardAgent=no', 'ubuntu@134.175.137.18')
REQUIRED_TRUE = {'app_health_and_private_gate_passed', 'candidate_schema_check_passed', 'no_credentials_or_rows_exported',
                 'original_values_preserved', 'process_dump_disabled', 'process_memory_locked', 'ram_mount_disables_swap',
                 'ram_workdir_removed', 'second_init_unchanged', 'snapshot_unchanged'}
REQUIRED_FALSE = {'apps_bound_to_network', 'production_deployed'}
MEDIA = {'external_urls', 'inline_data', 'local_or_unclassified_references'}
NUMBERS = {'snapshot', 'restore_migrate_verify'}
ERROR_PHASES = {'preflight', 'source_read_only_safety_check', 'snapshot', 'restore_and_migrate', 'verify_original_values',
                'verify_candidate_schema', 'verify_idempotent_initialization', 'cleanup'}
ERROR_TYPES = {'RuntimeError', 'TimeoutError', 'OSError', 'PermissionError', 'FileNotFoundError',
               'OperationalError', 'DatabaseError', 'ValueError', 'ImportError', 'MemoryError'}


def remote_command(tag):
    if not re.fullmatch('[a-f0-9]{24}', tag):
        raise ValueError('Invalid attempt tag')
    python = '/tmp/travel-release-rehearsal-20261116-3d8b3a0a/runtime-env-20261117-review1/venv/bin/python'
    shell = ('/usr/bin/mount --internal-only -t tmpfs -o size=64m,noswap,uid=1000,gid=1001,mode=0700,nodev,nosuid,noexec '
             'tmpfs /dev/shm && exec /usr/sbin/runuser -u ubuntu -- '
             + python + ' -I -B - production restore-' + tag)
    return ('sudo -n /usr/bin/prlimit --memlock=268435456:268435456 -- /usr/bin/unshare '
            '--mount --net --propagation private -- /bin/sh -c ' + shlex.quote(shell))


def safe_json_pairs(items):
    out = dict(items)
    if len(items) != len(out):
        raise ValueError('Duplicate receipt key')
    return out


def success(event, scope):
    fields = {'phase', 'scope', 'candidate_id', 'media_reference_types', 'original_tables_verified', 'snapshot_bytes',
              'sqlite_journal_mode_observed', 'synthetic_negative_checks', 'timings_seconds'} | REQUIRED_TRUE | REQUIRED_FALSE
    if not isinstance(event, dict) or event.keys() != fields or event.get('phase') != 'passed' or event.get('scope') != scope:
        return False
    if (event['candidate_id'] != CANDIDATE or any(event[k] is not True for k in REQUIRED_TRUE)
            or any(event[k] is not False for k in REQUIRED_FALSE)
            or type(event['original_tables_verified']) is not int or not 0 < event['original_tables_verified'] <= 128
            or type(event['snapshot_bytes']) is not int or not 0 < event['snapshot_bytes'] <= 16 * 1024 * 1024
            or event['sqlite_journal_mode_observed'] not in ('delete', 'persist', 'truncate')):
        return False
    media, timings, negatives = (event[k] for k in ('media_reference_types', 'timings_seconds', 'synthetic_negative_checks'))
    if (not isinstance(media, dict) or media.keys() != MEDIA or any(type(v) is not bool for v in media.values())
            or not isinstance(timings, dict) or timings.keys() != NUMBERS
            or any(type(v) not in (int, float) or not 0 <= v <= 65 or not math.isfinite(v) for v in timings.values())):
        return False
    if scope == 'production':
        return negatives is None
    return (isinstance(negatives, dict) and negatives.keys() == {
        'changed_photo_despite_sqlite_integrity_rejected', 'wal_header_rejected_before_sqlite_open'}
        and all(v is True for v in negatives.values()))


def classify(raw, exit_code):
    result = {'state': 'attempt_unconfirmed', 'ssh_exit': exit_code, 'cleanup_verified': False}
    if not raw or len(raw) > 4096:
        return result
    try:
        events = [json.loads(line, object_pairs_hook=safe_json_pairs) for line in raw.decode('utf-8').splitlines()]
    except (ValueError, RecursionError):
        return result
    if not events or not all(isinstance(e, dict) for e in events):
        return result
    first, final = events[0], events[-1]
    work = first.get('private_ram_workdir')
    started = (first.keys() == {'phase', 'private_ram_workdir'} and first['phase'] == 'started' and isinstance(work, str)
               and re.fullmatch(r'/dev/shm/travel-budget-restore-[a-z0-9_]{8}', work))
    if started:
        result['private_ram_workdir'] = work
    if exit_code == 0 and len(events) == 2 and started and success(final, 'production'):
        return {**result, 'state': 'production_copy_verified', 'cleanup_verified': True, 'report': final}
    if (exit_code == 1 and len(events) == (2 if started else 1) and
            final.keys() == {'phase', 'passed', 'error_type'} and final['passed'] is False and
            isinstance(final['phase'], str) and final['phase'] in ERROR_PHASES and
            isinstance(final['error_type'], str) and final['error_type'] in ERROR_TYPES):
        return {**result, 'state': 'reported_failure', 'failure_phase': final['phase'], 'error_type': final['error_type']}
    return result


def package():
    payload = SCRIPT.read_bytes()
    evidence = json.loads(EVIDENCE.read_bytes())
    synthetic = dict(evidence['enhanced_synthetic_receipt'])
    if (hashlib.sha256(payload).hexdigest() != SHA or evidence['runner_sha256'] != SHA or evidence['candidate_id'] != CANDIDATE
            or synthetic.pop('ssh_exit') != 0 or not synthetic.pop('private_workdir') or not success(synthetic, 'synthetic')):
        raise ValueError('Runner differs from the reviewed synthetic receipt')
    return payload


def run_once(payload):
    attempt = secrets.token_hex(12)
    if not ARCHIVE.is_dir() or ARCHIVE.resolve() != ARCHIVE:
        raise ValueError('Expected the existing local review directory')
    path = ARCHIVE / 'production-attempt.jsonl'
    meta = {'attempt': attempt, 'payload_sha256': SHA, 'candidate_id': CANDIDATE, 'production_ready': False}
    # This review permits one attempt. A missing final line is unconfirmed,
    # and a second invocation must not start SSH again under the same review.
    with path.open('x', encoding='utf-8') as f:
        f.write(json.dumps({**meta, 'state': 'starting'}) + '\n')
        f.flush()
        os.fsync(f.fileno())
        try:
            proc = subprocess.run([*SSH, remote_command(attempt)], input=payload, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=85)
        except subprocess.TimeoutExpired as e:
            result = {**classify(e.stdout, None), 'reason': 'observation_timeout'}
        except OSError:
            result = {'state': 'attempt_unconfirmed', 'reason': 'ssh_launch_failed', 'cleanup_verified': False}
        else:
            result = classify(proc.stdout, proc.returncode)
        result = {**meta, **result}
        f.write(json.dumps(result) + '\n')
    print(json.dumps({**result, 'receipt': path.relative_to(REPO).as_posix()}))
    return 0 if result['state'] == 'production_copy_verified' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Only for a separately authorized production copy rehearsal')
    parser.add_argument('--expected-sha256', help='The reviewed payload SHA256 for this approved attempt')
    args = parser.parse_args()
    if args.execute and args.expected_sha256 != SHA:
        parser.error('This action requires the exact reviewed SHA256 and separate approval')
    try:
        payload = package()
        if args.execute:
            return run_once(payload)
    except (ValueError, OSError, TypeError, KeyError) as e:
        parser.exit(1, f'Rehearsal controller refused: {type(e).__name__}\n')
    print(json.dumps({'state': 'local_payload_verified', 'remote_contacted': False, 'payload_bytes': len(payload),
                      'candidate_id': CANDIDATE, 'payload_sha256': SHA, 'production_ready': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
