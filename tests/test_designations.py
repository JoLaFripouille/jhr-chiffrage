"""Saved names are independent objects, durable offline and safe for old clients."""
from copy import deepcopy
import pytest
from starlette.testclient import TestClient
from jhr_chiffrage.core import Store, DomainError
from jhr_chiffrage.offline import OfflineStore
from jhr_chiffrage.server import build_app


def test_persistence_deduplication_receipts_and_removal(tmp_path):
    store = Store(tmp_path / 'names.sqlite3')
    data = {'name': '  cotation + label  '}
    first = store.save_designation(data, operation_id='one')
    assert first['name'] == 'cotation + label'
    assert store.save_designation(data, operation_id='one') == first
    assert store.save_designation({'name': 'COTATION + LABEL'}) == first
    assert Store(store.path).list_designations() == [first]
    removed = store.save_designation(dict(first, active=False), first['revision'])
    assert not removed['active'] and store.list_designations() == []
    with pytest.raises(DomainError, match='changé'):
        store.save_designation(dict(first, name='Stale'), first['revision'])
    backup = Store(store.backup())
    assert backup.list_designations() == []
    assert any(obj['kind'] == 'designation' and not obj['body']['active'] for obj in backup.sync_snapshot()['objects'])


@pytest.mark.parametrize('data', [{'name': ''}, {'name': '  '}, {'name': 3}, {'name': 'x' * 301}, {'name': 'ok', 'active': 1}])
def test_invalid_designations_rejected(tmp_path, data):
    with pytest.raises(DomainError):
        Store(tmp_path / 'names.sqlite3').save_designation(data)


class Network:
    def __init__(self, server):
        self.server = server
        self.online = True
    def sync_snapshot(self):
        if not self.online:
            raise DomainError('CONNECTION_ERROR', 'Offline')
        return self.server.sync_snapshot()
    def sync_push(self, changes, operation_id):
        if not self.online:
            raise DomainError('CONNECTION_ERROR', 'Offline')
        return self.server.sync_push(changes, operation_id=operation_id)
    def close(self):
        pass


def test_library_shared_offline_and_removed_on_second_pc(tmp_path):
    server = Store(tmp_path / 'server.sqlite3')
    config = dict(mode='server', url='https://test.invalid', token='test-only-token', ca_file='')
    network = Network(server)
    first = OfflineStore(config, remote=network, cache_dir=tmp_path / 'pc1')
    second = OfflineStore(config, remote=Network(server), cache_dir=tmp_path / 'pc2')
    network.online = False
    saved = first.save_designation({'name': 'cotation + label'})
    assert first.pending_count == 1 and not first.synchronize()
    reopened = OfflineStore(config, remote=network, cache_dir=tmp_path / 'pc1')
    assert reopened.list_designations() == [saved]
    network.online = True
    assert reopened.synchronize() and second.synchronize()
    assert second.list_designations() == [saved]
    second.save_designation(dict(saved, active=False), saved['revision'])
    assert second.synchronize() and reopened.synchronize()
    assert reopened.list_designations() == []


def test_snapshot_compatibility_and_rpc(tmp_path):
    store = Store(tmp_path / 'server.sqlite3')
    token = 'x' * 40
    with TestClient(build_app(store, token)) as client:
        def rpc(method, **params):
            return client.post('/api/v1/call', headers={'Authorization': 'Bearer ' + token}, json={'method': method, 'params': params})
        saved = rpc('save_designation', data={'name': 'cotation + label'}).json()['result']
        assert rpc('list_designations').json()['result'] == [saved]
        assert not any(obj['kind'] == 'designation' for obj in rpc('sync_snapshot').json()['result']['objects'])
        assert any(obj['kind'] == 'designation' for obj in rpc('sync_snapshot', include_designations=True).json()['result']['objects'])
        assert rpc('sync_snapshot', include_designations='yes').status_code == 400
        # Legacy client edits still work without removing newly introduced data.
        estimate = rpc('create_estimate', name='Legacy edit').json()['result']
        estimate['name'] = 'Legacy saved'
        assert rpc('save_estimate', data=estimate, expected_revision=estimate['revision']).status_code == 200
        assert store.list_designations() == [saved]


def test_conflicting_designations_preserve_both_names(tmp_path):
    server = Store(tmp_path / 'server.sqlite3')
    original = server.save_designation({'name': 'Cotation'})
    config = dict(mode='server', url='https://test.invalid', token='test-only-token', ca_file='')
    local = OfflineStore(config, remote=Network(server), cache_dir=tmp_path / 'pc')
    local.save_designation(dict(original, name='Cotation locale'), original['revision'])
    server.save_designation(dict(original, name='Cotation distante'), original['revision'])
    with pytest.raises(DomainError) as exc:
        local.synchronize()
    assert exc.value.code == 'SYNC_CONFLICT'
    local.resolve_conflict_keep_both()
    local.synchronize()
    assert {obj['name'] for obj in server.list_designations()} == {'Cotation locale', 'Cotation distante'}
