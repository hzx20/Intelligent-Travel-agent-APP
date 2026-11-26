"""Check the saved or served travel IP certificate. Never requests or deploys a certificate."""
import argparse
import hashlib
import hmac
import json
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import sys
import time


TARGET = '134.175.137.18'
CERT_NAME = 'travel-ip-public'
ACME = Path('/etc/travel-tls/acme')
WARN = 72 * 3600
CRITICAL = 36 * 3600
SHORT_MAX = 161 * 3600
PRODUCTION_CA = 'https://acme-v02.api.letsencrypt.org/directory'
WEBROOT = '/opt/continew/web'


def lifetime_status(expires, *, now):
    left = int(expires - now)
    if left <= 0:
        return 'expired', left
    if left <= CRITICAL:
        return 'critical', left
    if left <= WARN:
        return 'warning', left
    return 'ok', left


def exact_identity(identities):
    return tuple(identities) == (('IP Address', TARGET),)


def public_probe(*, expected_pem=None):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((TARGET, 443),timeout=6) as tcp:
        with context.wrap_socket(tcp,server_hostname=TARGET) as tls:
            peer = tls.getpeercert()
            if not exact_identity(peer.get('subjectAltName', ())):
                raise ValueError('served certificate has nonmatching or extra identities')
            expires = ssl.cert_time_to_seconds(peer['notAfter'])
            starts = ssl.cert_time_to_seconds(peer['notBefore'])
            if not 0 < expires - starts <= SHORT_MAX:
                raise ValueError('served certificate is not from a short lifetime profile')
            digest = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
    status, left = lifetime_status(expires,now=time.time())
    report = {'scope':'public_tls','status':status,'seconds_left':left,'fingerprint_sha256':digest,'matches_expected_leaf':None}
    if expected_pem:
        leaf = ssl.PEM_cert_to_DER_cert(expected_pem.read_text(encoding='ascii'))
        matches = hmac.compare_digest(hashlib.sha256(leaf).hexdigest(),digest)
        report['matches_expected_leaf'] = matches
        if not matches and status not in ('expired','critical'):
            report['status'] = 'served_leaf_differs'
    return report


def run_openssl(*options):
    result = subprocess.run(['/usr/bin/openssl',*options],check=True,capture_output=True,timeout=8,
                            env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    return result.stdout


def checked_lineage_path(root, basename, *, private=False):
    if basename not in ('cert.pem','chain.pem','fullchain.pem','privkey.pem'):
        raise ValueError('unexpected certificate path')
    if root.resolve(strict=True) != root or root.stat().st_uid!=0 or root.stat().st_mode & 0o077:
        raise ValueError('certificate account directory is not private and root owned')
    live = root/'live'/CERT_NAME
    archive = root/'archive'/CERT_NAME
    if live.resolve(strict=True)!=live or archive.resolve(strict=True)!=archive:
        raise ValueError('unexpected lineage directory alias')
    p = (live/basename).resolve(strict=True)
    info = p.stat()
    if (p.parent != archive or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or
        info.st_mode & (0o077 if private else 0o022) or not re.fullmatch(basename[:-4]+r'[1-9][0-9]*\.pem',p.name)):
        raise ValueError('unexpected lineage file, owner or permissions')
    return p


def parse_certificate_metadata(output):
    lines = output.decode('ascii').splitlines()
    parts = ('notBefore=', 'notAfter=')
    times = []
    for start in parts:
        fields = [l.removeprefix(start) for l in lines if l.startswith(start)]
        if len(fields)!=1:
            raise ValueError('certificate validity missing')
        times.append(ssl.cert_time_to_seconds(fields[0]))
    identities = [i for i,l in enumerate(lines) if l.strip() in (
        'X509v3 Subject Alternative Name:', 'X509v3 Subject Alternative Name: critical')]
    if len(identities)!=1 or [x.strip() for x in lines[identities[0]+1:]]!=['IP Address:'+TARGET]:
        raise ValueError('certificate SAN is not exactly the target IP')
    if not 0 < times[1] - times[0] <= SHORT_MAX:
        raise ValueError('certificate lifetime is not in the reviewed short profile')
    return times[1]


def pem_certificates(raw):
    blocks = re.findall(rb'-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----',raw)
    if not blocks or re.sub(rb'-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----', b'', raw).strip():
        raise ValueError('unexpected content in PEM chain')
    return [ssl.PEM_cert_to_DER_cert(block.decode('ascii')) for block in blocks]


def renewal_policy(text):
    section = False
    result, roots = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('['):
            section = line in ('[renewalparams]', '[[webroot_map]]')
            nested = line == '[[webroot_map]]'
            continue
        if not section or '=' not in line or line.startswith(('#',';')):
            continue
        key, value = (s.strip() for s in line.split('=',1))
        if nested:
            roots.append((key,value))
        elif key in result:
            raise ValueError('duplicate renewal parameter')
        else:
            result[key] = value
    # Explicit hooks in a lineage would bypass --no-directory-hooks.
    if any('hook' in key for key in result) or result.get('installer') not in (None,'None'):
        raise ValueError('unexpected certificate installer or hook')
    if (result.get('server')!=PRODUCTION_CA or result.get('authenticator')!='webroot' or
        (roots and roots!=[(TARGET,WEBROOT)]) or
        (not roots and result.get('webroot_path','').rstrip(',').strip()!=WEBROOT)):
        raise ValueError('certificate lineage does not match the reviewed CA or webroot')


def local_renewal_policy():
    file = ACME/'renewal'/f'{CERT_NAME}.conf'
    info = file.stat()
    if (file.resolve(strict=True) != file or info.st_uid != 0 or info.st_mode & 0o022 or
        not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024):
        raise ValueError('invalid renewal metadata location or permissions')
    renewal_policy(file.read_text(encoding='utf-8'))
    return {'scope':'renewal_policy','status':'ok'}


def local_lineage(*, paired_key=False):
    cert = checked_lineage_path(ACME,'cert.pem')
    chain = checked_lineage_path(ACME,'chain.pem')
    full = checked_lineage_path(ACME,'fullchain.pem')
    generation = re.fullmatch(r'cert(\d+)\.pem',cert.name)[1]
    if chain.name != 'chain'+generation+'.pem' or full.name != 'fullchain'+generation+'.pem':
        raise ValueError('certificate files changed during read')
    leaf = pem_certificates(cert.read_bytes())
    if len(leaf)!=1 or pem_certificates(full.read_bytes())!=leaf+pem_certificates(chain.read_bytes()):
        raise ValueError('full chain does not match the verified leaf and chain')
    run_openssl('verify','-purpose','sslserver','-verify_ip',TARGET,'-CAfile','/etc/ssl/certs/ca-certificates.crt',
                '-untrusted',str(chain),str(cert))
    expires = parse_certificate_metadata(run_openssl('x509','-in',str(cert),'-noout','-dates','-ext','subjectAltName'))
    if paired_key:
        private = checked_lineage_path(ACME,'privkey.pem',private=True)
        if private.name != 'privkey'+generation+'.pem' or not hmac.compare_digest(
            run_openssl('x509','-in',str(cert),'-pubkey','-noout'),run_openssl('pkey','-in',str(private),'-pubout')):
            raise ValueError('private key does not match certificate')
    status,left = lifetime_status(expires,now=time.time())
    return {'scope':'saved_lineage','status':status,'seconds_left':left,'key_match_checked':paired_key,
            'fingerprint_sha256':hashlib.sha256(leaf[0]).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope',choices=('saved','public','renewal'))
    parser.add_argument('--pair',action='store_true',help='Verify the saved leaf and private key match; only read public key output')
    parser.add_argument('--expect-saved',action='store_true',help='Require the public leaf to be the latest saved certificate')
    options = parser.parse_args()
    if (options.expect_saved and options.scope!='public') or (options.pair and options.scope!='saved'):
        parser.error('Invalid check for scope')
    try:
        if options.scope=='saved':
            report = local_lineage(paired_key=options.pair)
        elif options.scope=='renewal':
            report = local_renewal_policy()
        else:
            report = public_probe(expected_pem=checked_lineage_path(ACME,'cert.pem') if options.expect_saved else None)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        # Never put ACME logs, paths, private key material or raw hook errors in alerts.
        report = {'scope':options.scope,'status':'check_failed','error_type':type(error).__name__}
    print(json.dumps(report),file=sys.stdout if report['status']=='ok' else sys.stderr)
    return 0 if report['status']=='ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
