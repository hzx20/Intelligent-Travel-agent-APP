"""Send a rate-limited operational mail for a failed travel certificate check."""
import argparse
from contextlib import closing
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from getpass import getpass, GetPassWarning
import json
import os
from pathlib import Path
import smtplib
import sqlite3
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import warnings

if __package__:
    from . import travel_tls_check as tls
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import travel_tls_check as tls


REMINDER_INTERVAL = 12 * 3600
MAIL_CONFIG = Path('/etc/travel-tls/mail.json')
SUBJECTS = {
    'test': '旅行站证书告警测试',
    'renewal_failed': '旅行站证书续期或装载失败',
    'public_check_failed': '旅行站公网 HTTPS 检查失败',
    'saved_check_failed': '旅行站已保存证书检查失败',
    'warning': '旅行站证书将在 72 小时内到期',
    'critical': '旅行站证书将在 36 小时内到期',
    'expired': '旅行站证书已过期',
}


def parse_config(data):
    config = json.loads(data)
    required = {'host', 'port', 'security', 'sender', 'recipient', 'username', 'password'}
    if not isinstance(config, dict) or config.keys() != required:
        raise ValueError('Unexpected mail settings')
    if not all(isinstance(config[key], str) for key in required - {'port'}):
        raise ValueError('Unexpected mail setting types')
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
        raise ValueError('Invalid SMTP port')
    host = config['host']
    if not host or len(host) > 253 or not host.isascii() or any(not (c.isalnum() or c in '.-') for c in host):
        raise ValueError('Invalid SMTP host')
    if config['security'] not in ('implicit_tls', 'starttls'):
        raise ValueError('Verified SMTP TLS is required')
    if bool(config['username']) != bool(config['password']):
        raise ValueError('SMTP username and password must be supplied together')
    for key in ('sender', 'recipient'):
        address = config[key]
        if not address.isascii() or any(c in '\r\n' for c in address):
            raise ValueError('Invalid mail address')
        try:
            parsed = Address(addr_spec=address)
        except HeaderParseError as error:
            raise ValueError('Invalid mail address') from error
        if parsed.addr_spec != address or not parsed.username or not parsed.domain:
            raise ValueError('Expected a single address without a display name')
    return config


def credential(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or not 0 < info.st_size <= 8192:
            raise ValueError('Invalid private SMTP credential')
        return parse_config(stream.read(8193))


def write_private_config(path, config):
    output = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False)
    try:
        temporary = Path(output.name)
        with output:
            json.dump(config, output)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink()


def configure_qq(address):
    if sys.platform != 'linux' or os.geteuid() != 0 or not sys.stdin.isatty():
        raise ValueError('A private root terminal on the travel host is required')
    directory = MAIL_CONFIG.parent
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or directory.resolve() != directory:
        raise ValueError('Unexpected travel certificate configuration directory')
    if os.path.lexists(MAIL_CONFIG):
        raise FileExistsError('The current mail configuration must be preserved')
    config = {'host': 'smtp.qq.com', 'port': 465, 'security': 'implicit_tls',
              'sender': address, 'recipient': address, 'username': address, 'password': 'pending'}
    parse_config(json.dumps(config))
    if not address.lower().endswith('@qq.com'):
        raise ValueError('Expected a personal QQ mail account')
    with warnings.catch_warnings():
        warnings.simplefilter('error', GetPassWarning)
        secret = getpass('QQ 邮箱 SMTP 授权码（不回显）: ').strip()
    if not secret:
        raise ValueError('SMTP authorization code is required')
    config['password'] = secret
    write_private_config(MAIL_CONFIG, config)


def saved_expiry():
    try:
        leaf = tls.checked_lineage_path(tls.ACME, 'cert.pem')
        details = tls.run_openssl('x509', '-in', str(leaf), '-noout', '-dates', '-ext', 'subjectAltName')
        return tls.lifetime_status(tls.parse_certificate_metadata(details), now=time.time())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'unknown', None


def alert_details(event, expiry):
    status, seconds = expiry
    if event == 'saved' and status in ('warning', 'critical', 'expired'):
        code = status
    else:
        code = {'saved': 'saved_check_failed', 'renewal': 'renewal_failed', 'public': 'public_check_failed'}[event]
    lines = [SUBJECTS[code], f'入口 https://{tls.TARGET}/travel/']
    if seconds is not None:
        lines.append(f'原机保存证书的剩余有效期为 {max(0, seconds)//3600} 小时。')
    lines.append('请查看原服务器上对应的旅行证书检查日志。')
    return code, '\n'.join(lines) + '\n'


def send_mail(config, code, body):
    message = EmailMessage()
    message['From'] = config['sender']
    message['To'] = config['recipient']
    message['Subject'] = SUBJECTS[code]
    message.set_content(body)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    host, port = config['host'], config['port']
    if config['security'] == 'implicit_tls':
        client = smtplib.SMTP_SSL(host, port, timeout=8, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=8)
    with closing(client):
        if config['security'] == 'starttls':
            client.starttls(context=context)
        if config['username']:
            client.login(config['username'], config['password'])
        client.send_message(message)


def deliver(state, config, event, *, expiry, now, send=send_mail):
    code, body = alert_details(event, expiry)
    with closing(sqlite3.connect(state, timeout=12)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS mail_receipts (code TEXT PRIMARY KEY, sent_at INTEGER NOT NULL)')
        previous = db.execute('SELECT sent_at FROM mail_receipts WHERE code = ?', (code,)).fetchone()
        if previous and previous[0] <= now < previous[0] + REMINDER_INTERVAL:
            return {'status': 'suppressed', 'event': code}
        send(config, code, body)
        db.execute('INSERT INTO mail_receipts VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET sent_at=excluded.sent_at',
                   (code, now))
    return {'status': 'sent', 'event': code}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event', nargs='?', choices=('saved', 'renewal', 'public', 'test'))
    parser.add_argument('--configure-qq', metavar='ADDRESS')
    args = parser.parse_args()
    if bool(args.event) == bool(args.configure_qq):
        parser.error('Choose an alert task or the interactive QQ setup')
    if args.configure_qq:
        try:
            configure_qq(args.configure_qq)
        except (OSError, EOFError, ValueError, GetPassWarning) as error:
            print(json.dumps({'status': 'configuration_failed', 'error_type': type(error).__name__}), file=sys.stderr)
            return 1
        print(json.dumps({'status': 'qq_smtp_configured', 'mail_sent': False}))
        return 0
    try:
        config_path = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'smtp.json'
        config = credential(config_path)
        if args.event == 'test':
            send_mail(config, 'test', '这是旅行站证书告警测试。\n')
            report = {'status': 'sent', 'event': 'test'}
        else:
            state = Path(os.environ['STATE_DIRECTORY']) / 'mail-receipts.sqlite3'
            report = deliver(state, config, args.event, expiry=saved_expiry(), now=int(time.time()))
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        print(json.dumps({'status': 'delivery_failed', 'trigger': args.event, 'error_type': type(error).__name__}),
              file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
