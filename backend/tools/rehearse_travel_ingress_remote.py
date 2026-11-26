"""Exercise the exact nftables candidate using only anonymous test namespaces."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time


IP = '/usr/sbin/ip'
NFT = '/usr/sbin/nft'
BRIDGE = 'br-travel-edge'
GATEWAY = '172.31.240.1'
OTHER_DEST = '172.31.240.7'
GATEWAY6 = 'fd34:175:137::1'
REVIEWED = '707816e51b6926eea6814c275d8420f131407d07337a54b66ad9ee274c5c3b89'
MARKER = b'travel-ingress-fixture'
PHASE = 'isolation'


PROBE = '''
import json,socket,sys
try:
    with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=.8) as stream:
        stream.settimeout(.8)
        print(json.dumps({'connected':True,'marker':stream.recv(128).decode('ascii')}))
except TimeoutError:
    print(json.dumps({'connected':False,'timeout':True}))
'''


def require(ok):
    if not ok:
        raise ValueError('Ingress namespace verification failed')


def command(*args, data=None):
    return subprocess.run(args, input=data, capture_output=True, check=True, timeout=6).stdout


def stopped(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def isolated_peer(stack, name, address, ipv6=None):
    process = subprocess.Popen(('/usr/bin/unshare', '--net', '/usr/bin/sleep', '50'),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stack.callback(stopped, process)
    path = Path(f'/proc/{process.pid}/ns/net')
    current = os.readlink('/proc/self/ns/net')
    for _ in range(30):
        require(process.poll() is None)
        if os.readlink(path) != current:
            break
        time.sleep(.04)
    else:
        raise ValueError('Peer did not enter a separate network namespace')
    host_device = f'host-{name}'
    client_device = f'peer-{name}'
    command(IP, 'link', 'add', host_device, 'type', 'veth', 'peer', 'name', client_device, 'netns', str(process.pid))
    command(IP, 'link', 'set', host_device, 'master', BRIDGE)
    command(IP, 'link', 'set', host_device, 'up')
    inside = ('/usr/bin/nsenter', '--target', str(process.pid), '--net', IP)
    command(*inside, 'link', 'set', 'lo', 'up')
    command(*inside, 'addr', 'add', address+'/28', 'dev', client_device)
    if ipv6:
        command(*inside, '-6', 'addr', 'add', ipv6+'/64', 'dev', client_device, 'nodad')
    command(*inside, 'link', 'set', client_device, 'up')
    return process, host_device


def listener(stack, family, address, port):
    sock = stack.enter_context(socket.socket(family, socket.SOCK_STREAM))
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((address, port))
    sock.listen()

    def serve():
        while True:
            try:
                connection, _ = sock.accept()
                with connection:
                    connection.sendall(MARKER)
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()


def probe(process, address, port):
    return json.loads(command('/usr/bin/nsenter', '--target', str(process.pid), '--net',
                              '/usr/bin/python3', '-I', '-B', '-c', PROBE, address, str(port)))


def counters():
    raw = json.loads(command(NFT,'--json','list','chain','inet','travel_guard','backend_input'))['nftables']
    counts = {}
    for entry in raw:
        if 'rule' not in entry:
            continue
        expressions = entry['rule']['expr']
        action, = [action for action in ('accept','drop') if any(action in expr for expr in expressions)]
        total, = [expr['counter']['packets'] for expr in expressions if 'counter' in expr]
        counts[action] = total
    require(counts.keys() == {'accept','drop'})
    return counts


def actual_packets(rules):
    global PHASE
    ns = os.readlink('/proc/self/ns/net')
    require(os.geteuid() == 0 and sys.flags.isolated and ns != os.readlink('/proc/1/ns/net'))
    links = json.loads(command(IP, '-json', 'link', 'show'))
    require({link['ifname'] for link in links} == {'lo'})
    command(IP, 'link', 'set', 'lo', 'up')
    command(IP, 'link', 'add', BRIDGE, 'type', 'bridge')
    command(IP, 'addr', 'add', GATEWAY+'/28', 'dev', BRIDGE)
    command(IP, 'addr', 'add', OTHER_DEST+'/32', 'dev', BRIDGE)
    command(IP, '-6', 'addr', 'add', GATEWAY6+'/64', 'dev', BRIDGE, 'nodad')
    command(IP, 'link', 'set', BRIDGE, 'up')
    reachable = {'connected':True,'marker':MARKER.decode('ascii')}
    blocked = {'connected':False,'timeout':True}
    with ExitStack() as stack:
        proxy, proxy_device = isolated_peer(stack, 'proxy', '172.31.240.2', 'fd34:175:137::2')
        other, _ = isolated_peer(stack, 'other', '172.31.240.3')
        listener(stack, socket.AF_INET, '0.0.0.0', 8000)
        listener(stack, socket.AF_INET6, '::', 8000)
        listener(stack, socket.AF_INET, '0.0.0.0', 8001)
        PHASE = 'baseline'
        for peer, destination, port in ((proxy,GATEWAY,8000),(other,GATEWAY,8000),(proxy,OTHER_DEST,8000),
                                        (proxy,GATEWAY6,8000),(other,GATEWAY,8001)):
            require(probe(peer,destination,port) == reachable)
        PHASE = 'candidate_rules'
        command(NFT, '--file', '-', data=rules)
        command(NFT, '--file', '-', data=rules)
        require(counters() == {'accept':0,'drop':0})
        require(probe(proxy,GATEWAY,8000) == reachable and counters()['accept'] > 0)
        for peer, destination in ((other,GATEWAY),(proxy,OTHER_DEST),(proxy,GATEWAY6)):
            previous = counters()['drop']
            require(probe(peer,destination,8000) == blocked and counters()['drop'] > previous)
        require(probe(other,GATEWAY,8001) == reachable)

        PHASE = 'different_ingress_interface'
        command(NFT, 'delete', 'table', 'inet', 'travel_guard')
        command(IP, 'addr', 'del', GATEWAY+'/28', 'dev', BRIDGE)
        command(IP, 'link', 'set', proxy_device, 'nomaster')
        command(IP, 'addr', 'add', GATEWAY+'/28', 'dev', proxy_device)
        command('/usr/bin/nsenter', '--target', str(proxy.pid), '--net', IP,
                'neigh', 'flush', 'to', GATEWAY, 'dev', 'peer-proxy')
        require(probe(proxy,GATEWAY,8000) == reachable)
        command(NFT, '--file', '-', data=rules)
        require(probe(proxy,GATEWAY,8000) == blocked and counters()['drop'] > 0)
    require(proxy.poll() is not None and other.poll() is not None)
    return {'authorized_source_permitted':True,'same_bridge_other_source_dropped':True,'wrong_ipv4_destination_dropped':True,
            'ipv6_8000_dropped':True,'other_port_permitted':True,'same_source_wrong_interface_dropped':True,
            'repeated_load_idempotent':True,'test_client_processes_reaped':True}


def interrupted(*_):
    raise RuntimeError('Ingress test interrupted')


if __name__ == '__main__':
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        candidate = sys.stdin.buffer.read(16*1024)
        require(hashlib.sha256(candidate).hexdigest() == REVIEWED)
        print(json.dumps({'state':'isolated_packets_verified','checks':actual_packets(candidate)}))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'state':'not_verified','phase':PHASE,'error_type':type(error).__name__}))
        raise SystemExit(1) from None
