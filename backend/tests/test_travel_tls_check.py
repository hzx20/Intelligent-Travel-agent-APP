"""Check renewal input isolation and real certificate parsing without contacting a CA."""
from pathlib import Path
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading

import pytest

from tools import travel_tls_check as check


@pytest.mark.parametrize('seconds,state', [(72*3600+1,'ok'),(72*3600,'warning'),(36*3600+1,'warning'),(36*3600,'critical'),(0,'expired')])
def test_short_certificate_alarm_thresholds(seconds,state):
    assert check.lifetime_status(1000000+seconds,now=1000000)==(state,seconds)


def test_renewal_policy_refuses_staging_another_webroot_and_unreviewed_hooks():
    config = f'''[renewalparams]
authenticator = webroot
server = {check.PRODUCTION_CA}
webroot_path = {check.WEBROOT},
[[webroot_map]]
{check.TARGET} = {check.WEBROOT}
'''
    check.renewal_policy(config)
    for changed in (config.replace('acme-v02','acme-staging-v02'),config.replace(check.WEBROOT,'/tmp/site'),
                    config.replace('webroot\n','nginx\n',1),config.replace('[[webroot_map]]','renew_hook = /bin/false\n[[webroot_map]]')):
        with pytest.raises(ValueError):
            check.renewal_policy(changed)


def run(openssl, *args):
    out = subprocess.run([openssl,*map(str,args)],capture_output=True,timeout=10)
    assert out.returncode==0, out.stderr.decode(errors='replace')
    return out.stdout


@pytest.fixture
def materials(tmp_path):
    openssl = os.environ.get('TRAVEL_TEST_OPENSSL') or shutil.which('openssl')
    if not openssl:
        pytest.skip('OpenSSL required for one local TLS integration')
    with tempfile.TemporaryDirectory(dir=tmp_path) as name:
        path = Path(name)
        ca, ca_key = path/'root.pem',path/'root.key'
        run(openssl,'req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=Local synthetic test root','-days','7',
            '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign',
            '-keyout',ca_key,'-out',ca)
        certs = []
        for i,san in enumerate((f'critical,IP:{check.TARGET}',f'critical,IP:{check.TARGET}',f'IP:{check.TARGET},DNS:extra.fixture.test'),1):
            cnf, req, cert, key = (path/f'{i}.{ext}' for ext in ('cnf','csr','pem','key'))
            cnf.write_text(f'basicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName={san}\n')
            run(openssl,'req','-new','-newkey','rsa:2048','-nodes','-subj','/CN=Synthetic travel fence','-keyout',key,'-out',req)
            run(openssl,'x509','-req','-in',req,'-CA',ca,'-CAkey',ca_key,'-set_serial',i,'-days','5','-extfile',cnf,'-out',cert)
            certs.append((cert,key))
        yield openssl,ca,certs
    assert not path.exists()


def test_local_certificate_parsing_requires_exact_ip_and_valid_short_profile(materials):
    openssl,ca,certs = materials
    exact = run(openssl,'x509','-in',certs[0][0],'-noout','-dates','-ext','subjectAltName')
    wrong = run(openssl,'x509','-in',certs[2][0],'-noout','-dates','-ext','subjectAltName')
    assert check.parse_certificate_metadata(exact)>0
    with pytest.raises(ValueError):
        check.parse_certificate_metadata(wrong)
    leaf = check.pem_certificates(certs[0][0].read_bytes())
    assert check.pem_certificates(certs[0][0].read_bytes()+b'\n'+ca.read_bytes())==leaf+check.pem_certificates(ca.read_bytes())
    with pytest.raises(ValueError):
        check.pem_certificates(certs[0][0].read_bytes()+b'something else')


def test_public_monitor_distinguishes_correct_trusted_leaf_from_stale_leaf(materials,monkeypatch):
    _,ca,certs = materials
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # The probe closes after reading the leaf, before TLS 1.3 session tickets are needed.
    context.num_tickets = 0
    context.load_cert_chain(*map(str,certs[0]))
    client_context = ssl.create_default_context(cafile=str(ca))
    default_connect = socket.create_connection
    errors = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); listener.listen(2); listener.settimeout(5)
        port = listener.getsockname()[1]

        def server():
            try:
                for _ in range(2):
                    sock,_ = listener.accept()
                    with sock, context.wrap_socket(sock,server_side=True):
                        pass
            except Exception as error:
                errors.append(error)

        def local_connect(address,timeout):
            assert address==(check.TARGET,443)
            return default_connect(('127.0.0.1',port),timeout=timeout)

        monkeypatch.setattr(check.socket,'create_connection',local_connect)
        monkeypatch.setattr(check.ssl,'create_default_context',lambda:client_context)
        task = threading.Thread(target=server,daemon=True)
        task.start()
        assert check.public_probe(expected_pem=certs[0][0])['status']=='ok'
        assert check.public_probe(expected_pem=certs[1][0])['status']=='served_leaf_differs'
        task.join(5)
        assert not task.is_alive() and not errors
