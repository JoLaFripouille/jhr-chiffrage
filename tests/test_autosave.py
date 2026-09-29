"""Automatic saves are driven by edits, never by idle polling."""
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from jhr_chiffrage.core import Store, new_item, DomainError
from jhr_chiffrage.ui import MainWindow, uid


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path/'autosave.sqlite')
    settings=store.get_settings()
    settings.update(hourly_rate='80',levy_rate='20',vat_enabled=False)
    store.save_settings(settings,settings['revision'])
    widget=MainWindow(store)
    widget.timer.stop()
    widget.current=store.create_estimate('Test')
    widget.current['works']=[{'id':uid(),'name':'Work','items':[]}]
    widget.render()
    yield widget
    widget.dirty=widget.settings_dirty=False
    widget.close()


def test_save_after_post_and_no_writes_when_idle(window):
    window.autosave_post.setChecked(True)
    item=new_item('Post')
    item['duration_minutes']=25
    window.current['works'][0]['items'].append(item)
    before=window.current['revision']
    window.changed_tree()
    QApplication.processEvents()
    assert not window.dirty
    assert window.current['revision']==before+1
    assert window.store.get_estimate(window.current['id'])['works'][0]['items'][0]['duration_minutes']==25
    for _ in range(5):
        window.poll()
        window.autosave_current()
    assert window.current['revision']==before+1
    assert not window.autosave_timer.isActive()
    assert window.autosave_preferences.load()['after_post']


def test_delay_restarts_only_on_edits_and_manual_save_cancels(window):
    window.autosave_idle.setChecked(True)
    window.autosave_seconds.setValue(45)
    window.metadata['client'].setText('Edit')
    window.mark_dirty()
    assert window.autosave_timer.interval()==45000
    assert window.autosave_timer.isActive()
    assert window.dirty
    window.autosave_timer.stop()
    window.poll()
    assert not window.autosave_timer.isActive()
    window.mark_dirty()
    assert window.autosave_timer.isActive()
    assert window.save_current()
    assert not window.autosave_timer.isActive()


def test_modal_and_sync_defer_save_and_error_retains_draft(window,monkeypatch):
    window.autosave_post.setChecked(True)
    window.mark_dirty()
    monkeypatch.setattr(QApplication,'activeModalWidget',lambda:object())
    window.autosave_current()
    assert window.dirty and window.autosave_timer.interval()==500
    monkeypatch.setattr(QApplication,'activeModalWidget',lambda:None)
    window.sync_worker=object()
    window.autosave_current()
    assert window.dirty
    window.sync_worker=None
    window.autosave_timer.stop()
    def fail(*args,**kwargs): raise DomainError('REVISION_CONFLICT','Conflict')
    monkeypatch.setattr(window.store,'save_estimate',fail)
    window.autosave_current()
    assert window.dirty
    assert window.recovery.read()['estimate']
    assert not window.autosave_timer.isActive()
