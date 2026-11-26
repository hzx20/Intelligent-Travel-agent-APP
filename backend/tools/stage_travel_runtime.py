"""Prepare versioned application files and an offline Linux venv before cutover."""
import argparse
from hashlib import file_digest
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile

if __package__:
    from .release_candidate import verify as verify_application
    from .release_plan import pins, verify
else:
    from release_candidate import verify as verify_application
    from release_plan import pins, verify


TARGET = Path('/opt/travel')


def information(plan, expected):
    evidence = verify(plan, expected_id=expected)
    pinned = pins((plan/'ops/requirements-linux.lock').read_bytes(), hashed=True)
    return evidence, {name: item['version'] for name, item in pinned.items()}


def private_parent(root, *, production):
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError('Use a canonical staging parent')
    if not production:
        if root.parent != Path('/tmp') or not root.name.startswith('travel-runtime-stage-'):
            raise ValueError('Rehearsal requires its own temporary staging parent')
        return
    for parent in (root, *root.parents):
        info = parent.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Formal release parents must already be controlled by root')
    if os.geteuid() != 0:
        raise PermissionError('Formal staging requires root')


def outline_candidate(evidence):
    return {name: digest for name, digest in evidence['files'].items()
            if name.startswith(('application/', 'wheelhouse/')) or name == 'ops/requirements-linux.lock'}


def copy_pinned(source, target, digest):
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with source.open('rb') as source_file, target.open('xb') as target_file:
        shutil.copyfileobj(source_file, target_file)
    with target.open('rb') as reader:
        if file_digest(reader, 'sha256').hexdigest() != digest:
            raise ValueError('A staged input differs from the reviewed plan')
    target.chmod(0o644)


def python_call(command, *, limit=120):
    env = {name: value for name, value in os.environ.items() if not name.startswith(('PIP_', 'PYTHON'))}
    env.update({'PIP_CONFIG_FILE': os.devnull, 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_NO_CACHE_DIR': '1'})
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=limit)
    if result.returncode:
        raise RuntimeError(f'Offline environment command failed ({result.returncode})')
    return result.stdout


def stage(plan, evidence, pinned, root, *, production):
    if sys.platform != 'linux' or platform.machine() != 'x86_64' or sys.version_info[:3] != (3, 12, 3):
        raise ValueError('Run the installer on the verified Linux CPython 3.12.3 target')
    private_parent(root, production=production)
    if root == plan or root.is_relative_to(plan) or plan.is_relative_to(root):
        raise ValueError('Output and reviewed inputs must be disjoint')
    parents = (root/'releases', root/'runtime-envs')
    for parent in parents:
        parent.mkdir(mode=0o755, exist_ok=True)
        info = parent.lstat()
        if (parent.resolve(strict=True) != parent or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o022):
            raise ValueError('Unsafe versioned parent directory')
    release, runtime = (parent/evidence['application_id'] for parent in parents)
    if any(os.path.lexists(path) for path in (release, runtime)):
        raise FileExistsError('A versioned output already exists')
    created = []
    try:
        with tempfile.TemporaryDirectory(prefix='.travel-install-', dir=root) as scratch:
            scratch = Path(scratch)
            release.mkdir(mode=0o755)
            created.append(release)
            for name, digest in outline_candidate(evidence).items():
                target = release/name.removeprefix('application/') if name.startswith('application/') else scratch/name
                copy_pinned(plan/name, target, digest)
            verified = verify_application(release)
            if verified['id'] != evidence['application_id']:
                raise ValueError('The staged application differs from the reviewed identity')
            runtime.mkdir(mode=0o755)
            created.append(runtime)
            python_call([sys.executable, '-I', '-m', 'venv', '--copies', str(runtime)], limit=45)
            python = runtime/'bin/python'
            pip = [str(python), '-I', '-B', '-m', 'pip']
            python_call([*pip, 'install', '--no-index', '--require-hashes', '--only-binary=:all:',
                         '--find-links', str(scratch/'wheelhouse'), '-r', str(scratch/'ops/requirements-linux.lock')])
            python_call([*pip, 'check'], limit=25)
            listing = json.loads(python_call([*pip, 'list', '--format=json'], limit=25))
            installed = {re.sub(r'[-_.]+', '-', item['name']).lower(): item['version'] for item in listing}
            if {k: v for k, v in installed.items() if k in pinned} != pinned or set(installed)-set(pinned) != {'pip'}:
                raise ValueError('The installed dependency set differs from the Linux lock')
            smoke = "from io import BytesIO; from PIL import Image; p=BytesIO(); Image.new('RGB',(2,2)).save(p,'PNG'); assert Image.open(p).size==(2,2); import uvicorn,sqlite3,fastapi"
            python_call([str(python), '-I', '-B', '-c', smoke], limit=20)
            if verify_application(release)['id'] != verified['id']:
                raise ValueError('The staged application changed during offline installation')
            for tree in (release, runtime):
                for item in (tree, *tree.rglob('*')):
                    info = item.lstat()
                    if info.st_uid != os.geteuid() or (not item.is_symlink() and info.st_mode & 0o022):
                        raise ValueError('The staged runtime includes a writable or foreign-owned item')
            if production:
                probe = "import os,sys; assert os.access(sys.argv[1],os.R_OK) and not os.access(sys.argv[1],os.W_OK)"
                python_call(['/usr/sbin/runuser','-u','ubuntu','--',str(python),'-I','-B','-c',probe,
                             str(release/'backend/app/main.py')], limit=15)
    except BaseException:
        for path in reversed(created):
            shutil.rmtree(path)
        raise
    return {'application_id': evidence['application_id'], 'runtime_versions_match': True, 'pinned_dependencies': len(pinned),
            'pip_check_ok': True, 'image_and_runtime_imports_ok': True, 'formal_paths_created': production,
            'unit_installed': False, 'service_restarted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--expected-id', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stage-formal', action='store_true')
    mode.add_argument('--rehearse-under', type=Path)
    args = parser.parse_args()
    plan = args.plan.absolute()
    evidence, pinned = information(plan, args.expected_id)
    if not (args.stage_formal or args.rehearse_under):
        report = {'plan_id': evidence['id'], 'application_id': evidence['application_id'],
                  'pinned_dependencies': len(pinned), 'mode': 'review', 'files_staged': False}
    else:
        root = TARGET if args.stage_formal else args.rehearse_under.absolute()
        previous_umask = os.umask(0o022)
        try:
            report = stage(plan, evidence, pinned, root, production=args.stage_formal)
        finally:
            os.umask(previous_umask)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
