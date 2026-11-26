from email import policy
from email.parser import BytesParser
import json
import smtplib
import socket
import ssl
import sys
import threading

import pytest

from test_travel_tls_check import materials
from tools import travel_tls_mail as mail


CONFIG = {'host':'smtp.example.invalid','port':587,'security':'starttls',
          'sender':'ops@example.invalid','recipient':'owner@example.invalid',
          'username':'fixture-user','password':'fixture-password'}


@pytest.mark.parametrize('changed', [{'security':'plain'}, {'port':True}, {'host':'https://unsafe.invalid'},
                                     {'recipient':'owner@example.invalid\r\nBcc: other@example.invalid'},
                                     {'recipient':'<invalid>'}, {'password':''}])
def test_invalid_mail_configuration_is_rejected(changed):
    assert mail.parse_config(json.dumps(CONFIG)) == CONFIG
    with pytest.raises(ValueError):
        mail.parse_config(json.dumps({**CONFIG, **changed}))


def test_reminders_are_deduplicated_but_escalation_is_immediate(tmp_path):
    sent = []
    state = tmp_path/'receipts.sqlite3'

    def send(config, code, body):
        assert mail.tls.TARGET in body and config['password'] not in body
        sent.append(code)

    def alert(event, severity, now):
        return mail.deliver(state, CONFIG, event, expiry=(severity, 1000), now=now, send=send)

    assert alert('saved', 'warning', 100)['status'] == 'sent'
    assert alert('saved', 'warning', 200)['status'] == 'suppressed'
    assert alert('saved', 'critical', 201)['status'] == 'sent'
    assert alert('renewal', 'critical', 202)['status'] == 'sent'
    assert alert('public', 'critical', 203)['status'] == 'sent'
    assert alert('saved', 'warning', 100+mail.REMINDER_INTERVAL)['status'] == 'sent'
    assert sent == ['warning', 'critical', 'renewal_failed', 'public_check_failed', 'warning']


def test_failed_smtp_does_not_record_the_delivery(tmp_path):
    state = tmp_path/'receipts.sqlite3'

    def denied(*args):
        raise smtplib.SMTPServerDisconnected()

    with pytest.raises(smtplib.SMTPServerDisconnected):
        mail.deliver(state, CONFIG, 'renewal', expiry=('unknown', None), now=100, send=denied)
    assert mail.deliver(state, CONFIG, 'renewal', expiry=('unknown', None), now=101,
                        send=lambda *args: None)['status'] == 'sent'


def test_private_configuration_writer_preserves_existing_values(tmp_path):
    path = tmp_path/'mail.json'
    mail.write_private_config(path, CONFIG)
    with pytest.raises(FileExistsError):
        mail.write_private_config(path, {**CONFIG, 'password':'unwanted replacement'})
    assert json.loads(path.read_bytes()) == CONFIG
    assert list(tmp_path.iterdir()) == [path]


def test_starttls_refusal_prevents_authentication_and_mail(monkeypatch):
    moments = []

    class Server:
        def __init__(self, host, port, *, timeout):
            assert (host, port) == (CONFIG['host'], CONFIG['port'])

        def starttls(self, *, context):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            moments.append('tls')
            raise smtplib.SMTPNotSupportedError()

        def close(self):
            moments.append('closed')

    monkeypatch.setattr(mail.smtplib, 'SMTP', Server)
    with pytest.raises(smtplib.SMTPNotSupportedError):
        mail.send_mail(CONFIG, 'warning', 'temporary synthetic mail')
    assert moments == ['tls', 'closed']


def test_manual_test_uses_its_own_subject_and_no_certificate_failure_state(monkeypatch, tmp_path, capsys):
    sent = []
    monkeypatch.setattr(sys, 'argv', ['travel_tls_mail.py','test'])
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(tmp_path))
    monkeypatch.delenv('STATE_DIRECTORY', raising=False)
    monkeypatch.setattr(mail, 'credential', lambda path: CONFIG)
    monkeypatch.setattr(mail, 'send_mail', lambda config, code, body: sent.append((code, body)))
    assert mail.main() == 0
    assert json.loads(capsys.readouterr().out) == {'status':'sent','event':'test'}
    assert sent == [('test','这是旅行站证书告警测试。\n')]


def test_implicit_tls_sends_to_simulated_smtp_with_a_trusted_certificate(materials, monkeypatch):
    _, ca, certs = materials
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*map(str, certs[0]))
    client = ssl.create_default_context(cafile=str(ca))
    original_connection = socket.create_connection
    delivered, exceptions = [], []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]

        def receive():
            try:
                conn, _ = listener.accept()
                with conn, context.wrap_socket(conn, server_side=True) as secure, secure.makefile('rwb') as stream:
                    for reply, command in ((b'220 local SMTP', b'ehlo'), (b'250 hello', b'mail from:'),
                                           (b'250 sender', b'rcpt to:<owner@example.invalid>'), (b'250 recipient', b'data')):
                        stream.write(reply+b'\r\n')
                        stream.flush()
                        assert stream.readline().lower().startswith(command)
                    stream.write(b'354 input\r\n')
                    stream.flush()
                    data = []
                    for line in iter(stream.readline, b''):
                        if line == b'.\r\n':
                            break
                        data.append(line)
                    delivered.append(BytesParser(policy=policy.default).parsebytes(b''.join(data)))
                    stream.write(b'250 accepted\r\n')
                    stream.flush()
            except Exception as error:
                exceptions.append(error)

        def loopback(address, timeout, source_address=None):
            assert address == (mail.tls.TARGET, port)
            return original_connection(('127.0.0.1', port), timeout, source_address)

        monkeypatch.setattr(mail.ssl, 'create_default_context', lambda: client)
        monkeypatch.setattr(socket, 'create_connection', loopback)
        worker = threading.Thread(target=receive, daemon=True)
        worker.start()
        mail.send_mail({**CONFIG, 'host':mail.tls.TARGET, 'port':port, 'security':'implicit_tls',
                        'username':'','password':''}, 'expired', '合成测试邮件')
        worker.join(timeout=5)
    assert not worker.is_alive() and not exceptions
    assert delivered[0]['To'].addresses[0].addr_spec == CONFIG['recipient']
    assert str(delivered[0]['Subject']) == mail.SUBJECTS['expired']
    assert delivered[0].get_content().strip() == '合成测试邮件'
