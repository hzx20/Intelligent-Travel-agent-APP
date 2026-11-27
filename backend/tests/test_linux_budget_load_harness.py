"""Replay the Linux rehearsal's actual API workload locally, without SSH or production data."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx
import pytest

from tools import qa_linux_budget_load as qa
from tools.release_candidate import prepare, verify


FROZEN = Path(__file__).resolve().parents[2] / '.release-candidates/unified-application-2026-11-18-budget'

# 候选包是本机产物，.release-candidates/ 整目录在 .gitignore 里：CI 拿不到它，
# 直接读会 FileNotFoundError，整条流水线红。跟 nginx / dist 那几个用例一样，
# 材料不在就跳过，不要在没有材料的环境里假装测过。
pytestmark = pytest.mark.skipif(
    not FROZEN.is_dir(),
    reason='Frozen budget candidate required (local .release-candidates/, not in git)',
)


def test_rehearsal_patches_exact_frozen_file():
    original = (FROZEN / qa.ROUTER).read_bytes()
    expected = (FROZEN.parents[1] / qa.ROUTER).read_bytes()
    assert qa.patch_router(original) == expected
    with pytest.raises(RuntimeError):
        qa.patch_router(original + b'\n# unexpected change')


def test_actual_api_and_graph_load_harness_against_fixed_candidate(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FROZEN, source)
    (source / qa.ROUTER).write_bytes(qa.patch_router((source / qa.ROUTER).read_bytes()))
    candidate = tmp_path / 'application'
    metadata = prepare(source, source / 'frontend', candidate)
    (tmp_path / 'synthetic.env').write_text('JWT_SECRET=integration-key-for-synthetic-data-only\n')
    env = {k: os.environ[k] for k in ('PATH', 'SystemRoot', 'WINDIR') if k in os.environ}
    env.update(TRAVEL_DATA_DIR=str(tmp_path / 'data'), TRAVEL_ENV_FILE=str(tmp_path / 'synthetic.env'))
    log = tmp_path / 'service.log'
    with log.open('wb') as dst:
        process = subprocess.Popen([sys.executable, '-I', '-B', '-c', qa.BOOT, str(candidate), str(tmp_path)],
                                   env=env, stdin=subprocess.DEVNULL, stdout=dst, stderr=dst,
                                   cwd=candidate / 'backend', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic()+20
            while not (tmp_path / 'ready').is_file():
                assert process.poll() is None, log.read_text(errors='replace')
                assert time.monotonic() < deadline
                time.sleep(.05)
            url = 'http://127.0.0.1:' + (tmp_path / 'ready').read_text()
            with httpx.Client(base_url=url, trust_env=False, timeout=5) as client:
                while True:
                    assert process.poll() is None, log.read_text(errors='replace')
                    assert time.monotonic() < deadline
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.05)
                result = qa.workload(client)
                assert result['read_requests'] == 160 and result['write_statuses'] == [200, 409]
                assert result['real_sse_with_fake_provider']['done_ms'] >= 650
                assert (tmp_path / 'synthetic-chat-count').read_text() == '1\n'
                assert not (tmp_path / 'outbound-attempted').exists() and verify(candidate) == metadata
        finally:
            process.terminate()
            try:
                process.wait(7)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
