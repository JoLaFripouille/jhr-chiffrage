"""Diagnostic setup is checked in a separate process to preserve pytest hooks."""
import os
from pathlib import Path
import subprocess
import sys


def test_windowless_errors_and_native_trace_are_written(tmp_path):
    code = r"""
import faulthandler,sys,threading
from jhr_chiffrage.diagnostics import install,install_qt
sys.stdout = sys.stderr = None
path = install(sys.argv[1])
assert install(sys.argv[1]) == path
install_qt()
from PySide6.QtCore import qWarning
qWarning('diagnostic Qt test')
try:
    raise ValueError('diagnostic Python test')
except ValueError:
    sys.excepthook(*sys.exc_info())
def fail():
    raise RuntimeError('diagnostic worker test')
t = threading.Thread(target=fail)
t.start()
t.join()
faulthandler.dump_traceback(file=sys.stderr)
print('windowless output captured', flush=True)
"""
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    logs = list(tmp_path.glob('desktop-*.log'))
    assert len(logs) == 1
    text = logs[0].read_text(encoding='utf-8')
    for expected in ('Session start', 'Qt version=', 'diagnostic Qt test', 'diagnostic Python test',
                     'diagnostic worker test', 'Current thread', 'windowless output captured', 'Process exit'):
        assert expected in text


def test_repeated_tree_rebuilds_in_subprocess(tmp_path):
    code = r"""
import faulthandler,sys
from PySide6.QtWidgets import QApplication
from jhr_chiffrage.core import Store,new_item
from jhr_chiffrage.ui import MainWindow,uid,tree_nodes
faulthandler.enable()
a=QApplication([])
s=Store(sys.argv[1])
e=s.create_estimate('Test')
items=[]
for i in range(45):
    item=new_item('Test')
    item['duration_minutes']=15
    if i%3: item['parent_id']=items[i-i%3]['id']
    items.append(item)
e['works']=[{'id':uid(),'name':'Test','items':items}, {'id':uid(),'name':'Second','items':[]}]
e=s.save_estimate(e,e['revision'])
w=MainWindow(s)
w.timer.stop()
w.current=e
w.render()
w.show()
for i in range(120):
    w.work_tabs.setCurrentIndex(0)
    w.tree.topLevelItem(0).setExpanded(False)
    w.render_tree()
    assert not w.tree.topLevelItem(0).isExpanded()
    assert len(list(tree_nodes(w.tree)))==45
    w.work_tabs.setCurrentIndex(1)
    assert not list(tree_nodes(w.tree))
    a.processEvents()
w.close()
"""
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    result = subprocess.run([sys.executable,'-c',code,str(tmp_path/'stress.sqlite')], env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
