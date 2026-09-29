"""Move branches without losing identities, prices, descendants or draft safety."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication
from jhr_chiffrage import ui
from jhr_chiffrage.core import Store, new_item, calculate, move_item_branch, DomainError


@pytest.fixture
def works():
    rows=[new_item(name) for name in ('Root','Child','Grandchild','Great-grandchild','Other','Other child')]
    for index,item in enumerate(rows):
        item.update(duration_minutes=10, quantity=str(index+1))
    for index,parent in ((1,0),(2,1),(3,2),(5,4)):
        rows[index]['parent_id']=rows[parent]['id']
    return [{'id':'one','name':'First','items':rows}, {'id':'two','name':'Second','items':[]}]


@pytest.fixture
def window(tmp_path, works):
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'drag.sqlite')
    settings=store.get_settings()
    settings.update(hourly_rate='60',levy_rate='20',vat_enabled=False)
    store.save_settings(settings,settings['revision'])
    data=store.create_estimate('Moving')
    data['works']=works
    data=store.save_estimate(data,data['revision'])
    widget=ui.MainWindow(store)
    widget.timer.stop()
    widget.current=data
    widget.render()
    widget.show()
    app.processEvents()
    yield widget
    widget.dirty=widget.settings_dirty=False
    widget.close()


@pytest.mark.parametrize('placement',['before','after','inside'])
def test_move_full_branch_preserves_values_and_order(works,placement):
    original=copy.deepcopy(works)
    root,child,grandchild,great,other,otherchild=works[0]['items']
    moved=move_item_branch(works,'one',root['id'],'one',other['id'],placement)
    assert works==original
    items=moved[0]['items']
    assert {x['id'] for x in items}=={x['id'] for x in works[0]['items']}
    root_after=next(x for x in items if x['id']==root['id'])
    assert root_after['parent_id']==(other['id'] if placement=='inside' else None)
    for before in original[0]['items']:
        after=next(x for x in items if x['id']==before['id'])
        assert {k:v for k,v in after.items() if k!='parent_id'}=={k:v for k,v in before.items() if k!='parent_id'}
    assert next(x for x in items if x['id']==great['id'])['parent_id']==grandchild['id']
    if placement=='before': assert items[0]['id']==root['id']
    else: assert items[0]['id']==other['id']


def test_move_child_to_other_work_then_promote_to_root(works):
    root,child,grandchild,great,other,otherchild=works[0]['items']
    moved=move_item_branch(works,'one',child['id'],'two')
    assert [x['id'] for x in moved[1]['items']]==[child['id'],grandchild['id'],great['id']]
    assert moved[1]['items'][0]['parent_id'] is None
    moved=move_item_branch(moved,'two',child['id'],'one',other['id'],'inside')
    assert next(x for x in moved[0]['items'] if x['id']==child['id'])['parent_id']==other['id']
    moved=move_item_branch(moved,'one',child['id'],'one')
    assert next(x for x in moved[0]['items'] if x['id']==child['id'])['parent_id'] is None


def test_cycles_self_and_foreign_targets_are_rejected_without_mutation(works):
    original=copy.deepcopy(works)
    root,child,grandchild,great,*_=works[0]['items']
    for target in (root['id'],child['id'],grandchild['id'],great['id'],'missing'):
        with pytest.raises(DomainError):
            move_item_branch(works,'one',root['id'],'one',target,'inside')
    assert works==original


def test_move_saves_recovery_autosaves_and_keeps_overall_total(window):
    old=calculate(window.current)['ht_cents']
    root=window.current['works'][0]['items'][0]
    window.autosave_post.setChecked(True)
    payload={'estimate_id':window.current['id'],'work_id':'one','item_id':root['id']}
    window.move_post(payload,'two',None,'end')
    assert window.active_work_id=='two'
    assert window.recovery.read()['estimate']['works'][1]['items'][0]['id']==root['id']
    assert window.dirty
    assert calculate(window.current)['ht_cents']==old
    QApplication.processEvents()
    assert not window.dirty
    assert window.store.get_estimate(window.current['id'])['works']==window.current['works']
    assert window.tree.currentItem().data(0,Qt.UserRole)==root['id']


class Drop(QDropEvent):
    def __init__(self,point,mime,source):
        super().__init__(QPointF(point),Qt.MoveAction,mime,Qt.LeftButton,Qt.NoModifier)
        self.origin=source
    def source(self): return self.origin


@pytest.mark.parametrize('destination',['inside','before','after','tab','empty'])
def test_drag_queues_drop_until_native_drag_finishes(window,monkeypatch,destination):
    tree=window.tree
    first=tree.topLevelItem(0)
    other=tree.topLevelItem(1)
    if destination == "before":
        first, other = other, first
    tree.setCurrentItem(first)
    before=copy.deepcopy(window.current['works'])
    rect=tree.visualItemRect(other)
    if destination=='inside': point=rect.center()
    elif destination=='before': point=QPoint(rect.center().x(),rect.top()+1)
    elif destination=='after': point=QPoint(rect.center().x(),rect.bottom()-1)
    elif destination=='empty': point=QPoint(20,tree.viewport().height()-2)
    else: point=window.work_tabs.tabRect(1).center()
    class Drag:
        def __init__(self,parent): self.parent=parent
        def setMimeData(self,mime): self.mime=mime
        def exec(self,*args):
            event=Drop(point,self.mime,tree)
            if destination=='tab': window.work_tabs.dropEvent(event)
            else: tree.dropEvent(event)
            assert event.isAccepted()
            assert tree.pending_move
            assert window.current['works']==before
            assert tree.drag_active
            # Polling must not replace the source model during the drag loop.
            window.poll()
            return Qt.MoveAction
    monkeypatch.setattr(ui,'QDrag',Drag)
    tree.startDrag(Qt.MoveAction)
    assert not tree.drag_active
    assert tree.pending_move is None
    assert window.current['works']!=before
    assert calculate(window.current)['ht_cents']==21000
    if destination=='inside':
        assert window.tree.topLevelItemCount()==1
        assert window.tree.topLevelItem(0).childCount()==2
    elif destination=='tab':
        assert window.active_work_id=='two'
        assert len(window.current['works'][1]['items'])==4


def test_drag_cancellation_and_frozen_estimate(window,monkeypatch):
    tree=window.tree
    tree.setCurrentItem(tree.topLevelItem(0))
    before=copy.deepcopy(window.current)
    class Drag:
        def __init__(self,parent): pass
        def setMimeData(self,mime): pass
        def exec(self,*args): return Qt.IgnoreAction
    monkeypatch.setattr(ui,'QDrag',Drag)
    tree.startDrag(Qt.MoveAction)
    assert window.current==before
    assert not window.dirty
    payload=dict(tree.drag_context,item_id=tree.currentItem().data(0,Qt.UserRole))
    window.current['status']='frozen'
    window.render()
    assert not window.can_move_post(payload,'two',None,'end')
    tree.startDrag(Qt.MoveAction)
    assert window.current['works']==before['works']
