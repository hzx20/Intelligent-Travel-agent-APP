from contextlib import nullcontext
from subprocess import TimeoutExpired

import pytest

from tools import travel_tls_rotate as rotate


class IsolatedEdge:
    pointer = 'old'
    active = 'old'
    reloads = 0

    def locked(self):
        return nullcontext()

    def current(self):
        return self.pointer

    def stage(self, chain,key,validator):
        validator(chain,key)
        return 'new'

    def point(self,generation):
        self.pointer = generation

    def peer(self):
        return self.active

    def reload(self):
        self.reloads += 1
        self.active = self.pointer


def change(edge, *, wait=None, syntax=lambda:None, validator=lambda *_:None):
    return rotate.switch(edge,b'chain',b'key',validate_pair=validator,check_nginx=syntax,reload_nginx=edge.reload,
                         peer=edge.peer,wait=wait or (lambda probe,digest:probe()==digest))


def test_no_switch_if_working_served_generation_cannot_be_proved():
    edge = IsolatedEdge()
    edge.active = 'unexpected'
    assert change(edge)['state']=='old_leaf_unconfirmed'
    assert edge.reloads==0 and edge.pointer=='old'


def test_validation_failure_never_changes_or_reloads_the_existing_pointer():
    edge = IsolatedEdge()

    def reject(*_):
        raise ValueError('chain and key mismatch')

    with pytest.raises(ValueError):
        change(edge,validator=reject)
    assert edge.active==edge.pointer=='old' and edge.reloads==0


def test_unconfirmed_rollback_is_not_reported_as_healthy():
    edge = IsolatedEdge()
    sequence = iter((True,False,False))
    result = change(edge,wait=lambda *_:next(sequence))
    assert result=={'state':'rollback_unconfirmed','reload_sent':True,'rollback_verified':False}
    assert edge.reloads==2 and edge.pointer=='old'


def test_reload_command_timeout_still_restores_old_leaf_when_signal_could_have_arrived():
    edge = IsolatedEdge()
    original = edge.reload

    def timed_reload():
        original()
        if edge.reloads==1:
            raise TimeoutExpired('synthetic reload',1)

    edge.reload = timed_reload
    result = change(edge)
    assert result=={'state':'restored_previous','reload_sent':True,'rollback_verified':True}
    assert edge.reloads==2 and edge.pointer==edge.active=='old'
