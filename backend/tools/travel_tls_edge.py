"""First certificate and renewal loading for precisely one reviewed travel nginx."""
import argparse
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import travel_tls_check as tls, travel_tls_rotate as rotate
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import travel_tls_check as tls
    import travel_tls_rotate as rotate


ROOT = Path('/etc/travel-tls')
STORE = ROOT/'edge'
PIN = ROOT/'edge-target.json'
CONF = ROOT/'edge-nginx.conf'
NAME = '/travel-https-edge'
ROLE = 'travel-ip-https'
TARGET_CONF = '/etc/nginx/nginx.conf'
TARGET_STORE = '/etc/nginx/travel-tls'
TARGET_STATIC = '/usr/share/nginx/html/travel'
STATIC = '/opt/continew/web/travel'
DEDICATED_NORMAL = 'daf22f110166808d8b5f5f9d9241b1acd90e5dec002dc3d239606b1296347cef'
REVIEWED_CONFIG = {
    '222493b507c7737477061e944ec019d3b99f8b5d0c404d6e7d681790d50f5601': 'normal',
    'e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e': 'maintenance',
    DEDICATED_NORMAL: 'normal',
}
DOCKER = '/usr/bin/docker'
NGINX = '/usr/sbin/nginx'


def require(ok, issue):
    if not ok:
        raise ValueError(issue)


def read_root_file(path, max_bytes, *, public_certificate=False):
    require(path.resolve(strict=True) == path, 'Operator file is not canonical')
    mask = 0o022 if public_certificate else 0o077
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & mask
                and 0 < info.st_size <= max_bytes, 'Unexpected file owner, permissions or size')
        data = stream.read(max_bytes + 1)
        require(len(data) == info.st_size, 'Operator file changed during read')
        return data


def parse_target(raw):
    spec = json.loads(raw)
    require(isinstance(spec,dict) and spec.keys()=={'id','image','network','ip','config_sha256'},'Unreviewed target fields')
    require(all(isinstance(v,str) for v in spec.values()),'Invalid target field types')
    require(re.fullmatch('[a-f0-9]{64}',spec['id']) and re.fullmatch('sha256:[a-f0-9]{64}',spec['image']), 'Pin a container and image ID')
    require(re.fullmatch('[a-zA-Z0-9_.-]{1,64}',spec['network']) and spec['network'] not in ('host','none','bridge'),
            'Expected an explicitly approved bridge')
    ip = ipaddress.IPv4Address(spec['ip'])
    require(str(ip)==spec['ip'] and any(ip in ipaddress.IPv4Network(n) for n in ('172.16.0.0/12','10.0.0.0/8','192.168.0.0/16')),
            'Edge address must be an exact private IPv4')
    require(spec['config_sha256'] in REVIEWED_CONFIG,'Unexpected edge configuration')
    if spec['config_sha256'] == DEDICATED_NORMAL:
        require((spec['network'], spec['ip']) == ('travel-edge', '172.31.240.2'),
                'Dedicated configuration requires its reviewed edge network and address')
    return spec


def docker(*args):
    return subprocess.run([DOCKER,*args],stdin=subprocess.DEVNULL,capture_output=True,check=True,timeout=6,
                          env={'PATH':'/usr/bin:/bin','LC_ALL':'C'}).stdout


def verify_instance(meta, spec, *, unpublished=False):
    require(meta.get('Id')==spec['id'] and meta.get('Image')==spec['image'] and meta.get('Name')==NAME,
            'Container ID, name or image does not match')
    configuration, host, state = meta['Config'],meta['HostConfig'],meta['State']
    labels = configuration.get('Labels') or {}
    require(labels.get('travel.edge.role')==ROLE and state['Running'] is True and state['Restarting'] is False,
            'Container does not have the reviewed running edge role')
    require(meta['Path'] in ('nginx',NGINX) and meta['Args']==['-c',TARGET_CONF,'-g','daemon off;'],
            'Unexpected master command')
    require(configuration.get('User','') in ('','0','0:0','root') and host['ReadonlyRootfs'] is True
            and host['Privileged'] is False and host.get('PidMode','') in ('','private'),
            'Unexpected master ownership or filesystem mode')
    networks = meta['NetworkSettings']['Networks']
    require(set(networks)=={spec['network']} and networks[spec['network']]['IPAddress']==spec['ip'] and
            host['NetworkMode']==spec['network'],'Unexpected bridge identity')
    ports = host['PortBindings'] or {}
    if unpublished:
        require(not ports,'First certificate check requires all ports to remain unpublished')
    else:
        # Bind the reviewed public IPv4 only. Reachability is checked separately.
        require(ports=={'443/tcp':[{'HostIp':'0.0.0.0','HostPort':'443'}]},'Unexpected published ports')
    expected = {TARGET_CONF: str(CONF), TARGET_STORE: str(STORE)}
    allowed = {**expected, TARGET_STATIC: STATIC}
    if REVIEWED_CONFIG[spec['config_sha256']] == 'normal':
        expected[TARGET_STATIC] = STATIC
    require(set(host.get('Tmpfs') or {}) == {'/tmp'}, 'Expected only the dedicated /tmp tmpfs')
    binds = {}
    for mount in meta['Mounts']:
        target = mount['Destination']
        if mount['Type'] == 'tmpfs' and target == '/tmp':
            continue
        require(mount['Type'] == 'bind' and target in allowed and target not in binds
                and mount['Source'] == allowed[target] and mount['RW'] is False,
                'Unexpected container mount')
        binds[target] = mount['Source']
    require(expected.items() <= binds.items(), 'Missing required edge mount')


class Edge:
    def __init__(self, spec, *, unpublished=False):
        self.spec = spec
        self.unpublished = unpublished

    def guarded(self):
        container = self.spec['id']
        try:
            data = json.loads(docker('inspect','--type','container',container))
            require(isinstance(data, list) and len(data) == 1, 'Expected one exact container')
            verify_instance(data[0], self.spec, unpublished=self.unpublished)
        except (AttributeError, KeyError, TypeError) as error:
            raise ValueError('Unexpected container metadata') from error
        inside = docker('exec',container,'/usr/bin/sha256sum',TARGET_CONF).split()
        require(hashlib.sha256(read_root_file(CONF,64*1024)).hexdigest()==self.spec['config_sha256'] and
                inside and inside[0].decode('ascii')==self.spec['config_sha256'],'Configuration bytes changed')
        return container

    def check(self):
        docker('exec',self.guarded(),NGINX,'-t','-c',TARGET_CONF)

    def reload(self):
        require(not self.unpublished,'First certificate check cannot reload nginx')
        docker('exec',self.guarded(),NGINX,'-s','reload','-c',TARGET_CONF)

    def peer(self):
        container = self.guarded()
        output = docker('exec',container,'/usr/bin/curl','-q','--noproxy','*','--silent','--show-error','--max-time','3',
                        '--proto','=https','--cacert','/etc/ssl/certs/ca-certificates.crt',
                        '--connect-to',f'{tls.TARGET}:443:127.0.0.1:443','--output','/dev/null','--write-out','%{http_code}\n%{certs}',
                        f'https://{tls.TARGET}/travel/')
        status,_,certificates = output.partition(b'\n')
        expected = b'503' if REVIEWED_CONFIG[self.spec['config_sha256']]=='maintenance' else b'200'
        require(status==expected,'Edge travel response differs from reviewed mode')
        leaf = re.search(rb'-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----',certificates)
        require(leaf,'Verified edge handshake did not provide a leaf')
        return rotate.fingerprint(leaf[0])


def validate_pair(fullchain,key):
    raw = fullchain.read_bytes()
    certs = tls.pem_certificates(raw)
    require(2<=len(certs)<=6,'Expected leaf and public CA chain')
    with tempfile.TemporaryDirectory(prefix='travel-chain-check-') as scratch:
        leaf, chain = (Path(scratch)/leaf for leaf in ('leaf.pem','chain.pem'))
        leaf.write_text(ssl.DER_cert_to_PEM_cert(certs[0]),encoding='ascii')
        chain.write_text(''.join(ssl.DER_cert_to_PEM_cert(x) for x in certs[1:]),encoding='ascii')
        tls.run_openssl('verify','-purpose','sslserver','-verify_ip',tls.TARGET,'-CAfile','/etc/ssl/certs/ca-certificates.crt',
                        '-untrusted',str(chain),str(leaf))
        expires = tls.parse_certificate_metadata(tls.run_openssl('x509','-in',str(leaf),'-noout','-dates','-ext','subjectAltName'))
        require(tls.lifetime_status(expires,now=time.time())[0]=='ok','New certificate is too close to expiry')
        require(hmac.compare_digest(tls.run_openssl('x509','-in',str(leaf),'-pubkey','-noout'),
                                    tls.run_openssl('pkey','-in',str(key),'-pubout')),'Certificate and key do not match')


def saved_pair():
    tls.local_renewal_policy()
    full = tls.checked_lineage_path(tls.ACME,'fullchain.pem')
    priv = tls.checked_lineage_path(tls.ACME,'privkey.pem',private=True)
    require(full.name.removeprefix('fullchain') == priv.name.removeprefix('privkey'), 'Lineage changed during snapshot')
    return read_root_file(full,64*1024,public_certificate=True), read_root_file(priv,16*1024)


def bootstrap(store, *, pair_lookup=saved_pair, validate=validate_pair):
    with store.locked():
        require(not os.path.lexists(store.root/'current'),'First certificate already selected; refuse to replace')
        name = store.stage(*pair_lookup(),validate)
        store.point(name)
        return {'state':'first_pair_prepared','edge_started':False,'public_verified':False}


def verify_first(store, edge):
    require(edge.unpublished and REVIEWED_CONFIG[edge.spec['config_sha256']] == 'maintenance',
            'First check requires unpublished maintenance mode')
    with store.locked():
        current = store.current()
        edge.check()
        require(rotate.wait_for_leaf(edge.peer, current), 'First maintenance edge did not serve the expected leaf')
    return {'state':'first_maintenance_leaf_verified','public_verified':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',nargs='?',default='review',choices=('review','prepare-first','verify-first','reconcile'))
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    if args.mode=='review':
        print(json.dumps({'state':'review_only','candidate_configs':REVIEWED_CONFIG,'production_ready':False}))
        return 0
    if args.mode in ('prepare-first','reconcile') and not args.apply:
        parser.error('Mutating route requires explicit approved --apply')
    try:
        require(sys.platform=='linux' and os.geteuid()==0,'Expected root on the approved Linux host')
        rotate.private_directory(ROOT)
        store = rotate.Store(STORE)
        if args.mode=='prepare-first':
            result = bootstrap(store)
        else:
            spec = parse_target(read_root_file(PIN,4096))
            edge = Edge(spec, unpublished=args.mode=='verify-first')
            if args.mode=='verify-first':
                result = verify_first(store, edge)
            else:
                result = rotate.switch(store,*saved_pair(),validate_pair=validate_pair,check_nginx=edge.check,
                                       reload_nginx=edge.reload,peer=edge.peer)
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
        result = {'state':'check_failed','error_type':type(error).__name__}
    ok = result['state'] in ('first_pair_prepared','first_maintenance_leaf_verified','switched','unchanged')
    print(json.dumps(result),file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__=='__main__':
    raise SystemExit(main())
