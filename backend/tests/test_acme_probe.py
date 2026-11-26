import io
import json

import pytest

from tools import run_acme_probe as probe


NONCE = 'f0' * 12
NAME = 'travel-acme-probe-' + NONCE
BODY = f'travel-acme-probe:{NONCE}\n'.encode()


class FakeResponse(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status


@pytest.mark.parametrize('status,data,ok', [(200,BODY,True),(200,b'SPA header',False),(200,BODY+b'x',False),(308,BODY,False)])
def test_public_probe_requires_exact_bytes_without_following_redirect(status,data,ok):
    called = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == (probe.TARGET, 80, 5)

        def request(self, method, path, *, headers):
            called.append((method,path))
            assert headers['Accept-Encoding']=='identity'

        def getresponse(self):
            return FakeResponse(status,data)

        def close(self):
            pass

    assert probe.public_body_matches(NAME,BODY,connect=Connection) is ok
    assert called == [('GET','/.well-known/acme-challenge/'+NAME)]


class Inbound(io.StringIO):
    def close(self):
        self.captured = self.getvalue()
        super().close()


class Remote:
    def __init__(self, events, exit_code):
        self.stdout = io.StringIO('\n'.join(json.dumps(e) for e in events)+'\n')
        self.stdin = Inbound()
        self.exit_code = exit_code

    def wait(self, timeout):
        assert self.stdin.closed
        return self.exit_code


@pytest.mark.parametrize('right_token,verified,exit_code', [(True,True,0),(False,True,1),(True,False,1),(True,True,255)])
def test_ssh_exit_and_cleanup_are_required_after_public_match(right_token,verified,exit_code):
    ready = {'event':'ready','name':NAME,'body_sha256':probe.sha(BODY)}
    cleanup = {'event':'cleanup','verified':verified,'caller_confirmed_exact_body':right_token}
    remote = Remote([ready,cleanup],exit_code)
    fetches = []

    def fetch(name,body):
        fetches.append((name,body))
        return right_token

    result = probe.evaluate(remote,NONCE,fetch=fetch)
    assert fetches == [(NAME,BODY)]
    assert remote.stdin.captured == ('exact-match\n' if right_token else '')
    assert (result['state']=='http01_token_verified') is (right_token and verified and exit_code == 0)


def test_forged_ssh_readiness_never_triggers_public_request():
    remote = Remote([{'event':'ready','name':NAME,'body_sha256':'0'*64}],1)

    def forbidden(*args):
        pytest.fail('Must not contact the public endpoint after a mismatched ready event')

    assert probe.evaluate(remote,NONCE,fetch=forbidden)['state']=='http01_not_verified'
    assert remote.stdin.captured == ''
