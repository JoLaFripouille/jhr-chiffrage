"""Crash recovery must survive abrupt process termination without saving to the DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from jhr_chiffrage.core import Store
from jhr_chiffrage.recovery import RecoveryFile
from jhr_chiffrage.ui import MainWindow, ItemDialog


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
    store = Store(tmp_path/'data.sqlite')
    settings = store.get_settings()
    settings.update(hourly_rate='80',levy_rate='20',vat_enabled=False)
    store.save_settings(settings,settings['revision'])
    widget = MainWindow(store)
    widget.timer.stop()
    yield widget
    widget.dirty=widget.settings_dirty=False
    widget.close()


def test_atomic_write_failure_preserves_previous_draft(window, monkeypatch):
    recovery=window.recovery
    recovery.write({'estimate': {'name':'recover me'}})
    def fail(*args): raise OSError('simulated disk failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError): recovery.write({'estimate': {'name':'new'}})
    assert recovery.read()['estimate']['name']=='recover me'


def test_abrupt_exit_then_restore_and_save(window):
    code = r"""
import os,sys
from PySide6.QtWidgets import QApplication
from jhr_chiffrage.core import Store,new_item
from jhr_chiffrage.ui import MainWindow,uid
app=QApplication([])
s=Store(sys.argv[1])
e=s.create_estimate('Before crash')
w=MainWindow(s)
w.current=e
w.render()
w.current['works']=[{'id':uid(),'name':'Unsaved work','items':[dict(new_item('Unsaved post'),duration_minutes=35)]}]
w.changed_tree()
w.metadata['client'].setText('Unsaved client')
w.mark_dirty()
os._exit(9)
"""
    result=subprocess.run([sys.executable,'-c',code,str(window.store.path)], env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),timeout=30)
    assert result.returncode==9
    persisted=window.store.get_estimate(window.store.list_estimates()[0]['id'])
    assert persisted['works']==[]
    window.restore_recovery()
    assert window.dirty
    assert window.current['works'][0]['items'][0]['duration_minutes']==35
    assert window.metadata['client'].text()=='Unsaved client'
    assert window.store.get_estimate(persisted['id'])['works']==[]
    assert window.save_current()
    assert window.recovery.read() is None
    assert window.store.get_estimate(persisted['id'])['works']


def test_in_progress_item_and_settings_are_in_safety_copy(window):
    from jhr_chiffrage.ui import uid
    window.current=window.store.create_estimate('Example')
    window.current['works']=[{'id':uid(),'name':'Work','items':[]}]
    window.render()
    window.changed_tree()
    dialog=ItemDialog(parent=window)
    dialog.label.setText('Still typing')
    dialog.fields['hours'].minutes.setValue(25)
    assert window.recovery.read()['editor']['label']=='Still typing'
    assert window.recovery.read()['editor']['duration_minutes']==25
    window.setting_fields['hourly_rate'].setText('95')
    window.settings_changed()
    assert window.recovery.read()['settings']['hourly_rate']=='95'
    dialog.reject()
    assert window.recovery.read()['editor'] is None
    assert window.recovery.read()['estimate']['works']
    assert window.save_current()
    assert 'estimate' not in window.recovery.read()
    assert window.save_settings()
    assert window.recovery.read() is None


def test_recovery_never_overwrites_newer_server_version(window):
    original=window.store.create_estimate('Original')
    window.recovery.write({'estimate':dict(original,client='Recovered client')})
    latest=window.store.save_estimate(dict(original,client='Other PC'),original['revision'])
    window.restore_recovery()
    assert window.current['id'] != original['id']
    assert window.current['client']=='Recovered client'
    assert window.save_current()
    assert window.store.get_estimate(original['id'])==latest
