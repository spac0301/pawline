"""Synthetic Qt interaction checks, usable on Linux offscreen and native Windows.

No provider request, account login, private log or personal sprite is needed.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw
from PySide6.QtTest import QTest
from PySide6.QtGui import QPalette
from fluff_monitor import qt_pet as ui

out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
app=ui.QApplication([])
ui.QFontDatabase.addApplicationFont(str(ui.ROOT/'assets/fonts/PretendardVariable.ttf'))

def drain(seconds=.2):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        app.processEvents();time.sleep(.005)

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);pack=root/'pet';pack.mkdir()
    atlas=Image.new('RGBA',(1536,1872));draw=ImageDraw.Draw(atlas)
    for row in range(9):draw.rounded_rectangle((20,row*208+20,172,row*208+195),20,fill=(240,200,220,255))
    atlas.save(pack/'spritesheet.png');(pack/'pet.json').write_text(json.dumps(dict(id='fixture',name='Fixture',spritesheetPath='spritesheet.png')))
    settings={};saved=[]
    panel=ui.Panel(root,settings,lambda:saved.append(dict(settings)))
    pet=ui.PetWindow(pack,panel);panel.pet=pet
    pet.move(580,270);pet.show();panel.pinned=True;panel.reveal()
    now=time.time();usage=dict(last=dict(input_tokens=10000,cached_input_tokens=9680,output_tokens=700,cached_fraction=.968),observed_at=now)
    route=dict(thread_id='task-a',activity='running',title='Cross-Vendor AI 회의 보드 구현',requested='gpt-6-astra',effort='max',served='gpt-6-astra',verdict='ok',usage=usage,
               sessions=[dict(thread_id='task-a',title='첫 번째 작업'),dict(thread_id='task-b',title='두 번째 작업')])
    claude=dict(title='Claude 구현 담당 · 저장 자료 검토',requested_model='claude-opus-5-5',requested_effort='max',served='claude-opus-5-5',label='작업 중',state='running',usage=usage,
                sessions=[dict(session_id='current',selection_key='role:implementation',title='구현 담당')])
    panel.update_data(route,claude);drain()
    assert (panel.width(),panel.height())==(320,307)
    widgets=[panel.gpt.title.caption,panel.gpt.served,panel.gpt.cache,panel.anthropic.title.caption,panel.anthropic.served,panel.anthropic.cache]
    columns=[w.mapTo(panel,ui.QPoint(0,0)).x() for w in widgets]
    assert columns[:3]==columns[3:],columns
    assert columns[0]==columns[2],columns
    assert columns[0]==panel.gpt.mark.mapTo(panel,ui.QPoint(0,0)).x(),columns
    assert columns[0]==panel.app_name.mapTo(panel,ui.QPoint(0,0)).x(),columns
    assert columns[1]==panel.gpt.cache_metric.mapTo(panel,ui.QPoint(0,0)).x(),columns
    assert columns[4]==panel.anthropic.cache_metric.mapTo(panel,ui.QPoint(0,0)).x(),columns
    assert panel.gpt.cache.fontMetrics().horizontalAdvance(panel.gpt.cache.text()) <= panel.gpt.cache.width()
    assert panel.gpt.cache_metric.fontMetrics().horizontalAdvance(panel.gpt.cache_metric.text()) <= panel.gpt.cache_metric.width()
    assert panel.gpt.cache_metric.fontMetrics().elidedText(panel.gpt.cache_metric.text(),ui.Qt.TextElideMode.ElideRight,panel.gpt.cache_metric.width())==panel.gpt.cache_metric.text()
    weight_axis=ui.QFont.Tag.fromString('wght')
    for label in (panel.gpt.title.caption,panel.gpt.served,panel.gpt.cache_metric):
        assert label.font().variableAxisValue(weight_axis)==label.font().weight()
    typography={name:dict(family=w.font().family(),pixels=w.font().pixelSize(),weight=w.font().weight(),color=w.palette().color(QPalette.ColorRole.WindowText).name())
                for name,w in [('title',panel.gpt.title.caption),('model',panel.gpt.served),('metric',panel.gpt.cache_metric),('caption',panel.gpt.cache)]}
    assert [v['pixels'] for v in typography.values()]==[14,13,13,12],typography
    assert [v['weight'] for v in typography.values()]==[600,500,600,400],typography
    for w in (panel.gpt.served,panel.anthropic.served):
        assert w.fontMetrics().horizontalAdvance(w.text())<=w.width()
    arrow_edges=[w.mapTo(panel,ui.QPoint(w.width(),0)).x() for section in (panel.gpt,panel.anthropic)
                 for w in section.findChildren(ui.ControlMark)]
    assert len(set(arrow_edges))==1,arrow_edges
    assert panel.gpt.title.caption.x()==8 and panel.anthropic.title.caption.x()==8
    assert panel.anthropic.requested.text() == 'claude-opus-5.5 · max'
    assert panel.anthropic.served.text() == 'claude-opus-5.5'
    assert claude['served'] == 'claude-opus-5-5'
    assert panel.gpt.cache.text()=='입력 캐시' and panel.gpt.cache_metric.text()=='96.8%'
    assert panel.gpt.cache.text()==panel.anthropic.cache.text()
    assert '전체 입력' in panel.gpt.cache.toolTip()
    assert panel.gpt.progress.text()=='작업 중' and panel.anthropic.progress.text()=='작업 중'
    assert panel.gpt.status.text()=='모델 일치' and panel.anthropic.status.text()=='기록 확인'
    assert panel.pin_toggle.isChecked() and panel.pin_toggle.toolTip()=='창 고정 해제'
    panel.pin_toggle.click();drain()
    assert not panel.pinned and panel.pin_toggle.toolTip()=='창 고정'
    panel.pin_toggle.click();drain()
    assert panel.pinned and panel.pin_toggle.toolTip()=='창 고정 해제'
    assert set(panel.pin_toggle.parentWidget().findChildren(ui.QPushButton))=={panel.pin_toggle,panel.theme_button}
    assert not hasattr(panel,'options_button')
    panel.theme_button.click();drain()
    assert settings['theme']=='light' and panel.pinned and panel.theme_mark.name=='moon'
    panel.theme_button.click();drain()
    assert settings['theme']=='dark' and panel.pinned and panel.theme_mark.name=='sun'
    assert panel.gpt.mode.text()==panel.anthropic.mode.text()=='자동 추적'
    panel.grab().save(str(out/'panel-dark.png'))
    stopped = dict(route, requested=None, served=None, configured_model='gpt-6-astra',
                   configured_effort='max', verdict='UNKNOWN', observation_disabled='observation_queue_limit')
    panel.update_data(stopped, claude);drain()
    assert panel.gpt.requested.caption_label.text() == '설정'
    assert panel.gpt.requested.text() == 'gpt-6-astra · max'
    assert panel.gpt.served.text() == '—'
    assert panel.gpt.status.text() == '관측 중단'
    assert panel.gpt.cache.text()=='입력 캐시' and panel.gpt.cache_metric.text()=='96.8%'
    panel.grab().save(str(out/'capture-stopped.png'))
    panel.update_data(route, claude);drain()
    assert panel.gpt.requested.caption_label.text() == '요청'
    panel.update_data(dict(route,title='Cross-Vendor AI 회의 보드 구현 · 최근 작업 상세'),
                      dict(claude,title='Claude 구현 담당 · 도착 예측 검토 · 최근 작업 상세'));drain()
    expanded=[]
    for name,anchor in [('gpt',panel.gpt.title),('claude',panel.anthropic.title)]:
        before=[w.mapTo(panel,ui.QPoint(0,0)) for w in widgets]
        panel.title_popup.reveal(anchor);drain(.3)
        assert panel.title_popup.isVisible(),name
        assert panel.title_popup.width()>anchor.width(),(name,panel.title_popup.width(),anchor.width())
        assert panel.title_popup.height()==anchor.height()
        assert (panel.width(),panel.height())==(320,307)
        assert [w.mapTo(panel,ui.QPoint(0,0)) for w in widgets]==before
        assert panel.title_popup.button.caption.x()==8
        assert not panel.title_popup.geometry().intersects(ui.QRect(*panel.pet_rect()))
        panel.title_popup.grab().save(str(out/(name+'-title.png')))
        expanded.append(dict(provider=name,title_width=panel.title_popup.width(),card=[320,307]))
        panel.title_popup.hide()
    for provider, section in [('gpt', panel.gpt), ('claude', panel.anthropic)]:
        for kind, button in [('cache', section.cache_button), ('status', section.status_button)]:
            before=(panel.width(),panel.height())
            button.click();drain(.3)
            popup=panel.detail_popup
            assert popup.isVisible(),(provider,kind)
            assert popup.body.text() == (section.cache.toolTip() if kind=='cache' else section.status.toolTip())
            if kind=='cache':
                assert popup.cache_graph.isVisible() and not popup.body.isVisible()
                assert popup.cache_graph.data['cached']==9680 and popup.cache_graph.data['other']==320
                assert popup.height()<=170,popup.height()
            else:
                assert not popup.cache_graph.isVisible() and popup.body.isVisible()
                assert popup.body.height() >= popup.body.heightForWidth(popup.body.width())
                assert popup.body.y()+popup.body.height() <= popup.height()-12
                assert popup.body.y() >= popup.heading.y()+popup.heading.height()+8
                assert popup.height()<=140,popup.height()
            assert popup.heading.text().startswith(section.brand.text()+' · ')
            assert not popup.geometry().intersects(ui.QRect(*panel.pet_rect()))
            assert before==(panel.width(),panel.height())
            popup.grab().save(str(out/(provider+'-'+kind+'-detail.png')))
            QTest.keyClick(popup,ui.Qt.Key.Key_Escape);drain()
            assert not popup.isVisible() and panel.pinned
    # A graph must shrink to a real empty state, not show a zero-percent bar.
    panel.gpt.cache_button.click();drain()
    panel.update_data(dict(route,usage=None),claude);drain()
    assert panel.gpt.cache.text()=='입력 캐시' and panel.gpt.cache_metric.text()=='기록 대기'
    assert columns[1]==panel.gpt.cache_metric.mapTo(panel,ui.QPoint(0,0)).x()
    assert not panel.detail_popup.cache_graph.data['known']
    assert panel.detail_popup.height()<100,panel.detail_popup.height()
    panel.detail_popup.grab().save(str(out/'cache-waiting.png'))
    panel.detail_popup.hide()
    panel.show_details(panel.gpt.status_button,'GPT · 모델 관측','새 응답의 모델명을 기다리고 있어요.');drain()
    assert panel.detail_popup.height()<90,panel.detail_popup.height()
    panel.detail_popup.grab().save(str(out/'short-status-detail.png'))
    panel.detail_popup.hide()
    panel.update_data(route, claude)
    choices=[]
    for provider in ('gpt','claude'):
        panel.choose_provider(provider);drain()
        assert panel.popup.isVisible()
        assert not panel.popup.geometry().intersects(ui.QRect(*panel.pet_rect()))
        positions=[]
        for button in panel.popup.entries:
            label=button.layout().itemAt(1).widget()
            positions.append(label.x())
        assert len(set(positions))==1,positions
        choices.append(positions[0])
        panel.popup.grab().save(str(out/(provider+'-menu.png')))
        panel.popup.entries[-1].click();drain()
        assert not panel.popup.isVisible()
    assert choices[0]==choices[1]
    assert settings['codex_thread']=='task-b'
    assert settings['claude_session']=='role:implementation'
    panel.update_data(route,claude);drain()
    assert panel.gpt.mode.text()==panel.anthropic.mode.text()=='직접 선택'
    panel.grab().save(str(out/'manual-selection.png'))
    panel.choose_provider('gpt');drain()
    assert app.activePopupWidget() is panel.popup
    QTest.keyClick(panel.popup,ui.Qt.Key.Key_Escape);drain()
    assert not panel.popup.isVisible()
    for index in (0,1,1):
        panel.actions('pet');drain()
        actions=[b.accessibleName() for b in panel.popup.entries]
        assert actions==['쓰다듬기','산책 끄기' if pet.walking else '산책 켜기','펫 종료'],actions
        assert [b for b in panel.popup.findChildren(ui.QPushButton) if b.isVisible()]==panel.popup.entries
        assert all('고정' not in action and '화면' not in action for action in actions)
        assert not panel.popup.geometry().intersects(pet.geometry())
        assert not panel.popup.geometry().intersects(panel.geometry())
        panel.popup.grab().save(str(out/'pet-actions.png'))
        panel.popup.entries[index].click();drain()
        assert not panel.popup.isVisible() and panel.pinned and panel.isVisible()
    panel.actions('pet');drain()
    QTest.keyClick(panel.popup,ui.Qt.Key.Key_Escape);drain()
    assert not panel.popup.isVisible() and panel.pinned
    panel.actions('pet');panel.theme_button.click();drain()
    assert not panel.popup.isVisible() and panel.pinned and settings['theme']=='light'
    panel.theme_button.click();drain()
    assert settings['theme']=='dark' and panel.pinned
    panel.choose_provider('gpt');drain()
    assert panel.popup.width()==276
    panel.popup.hide()
    settings['theme']='light';panel.apply_theme();drain()
    assert panel.gpt.cache_metric.fontMetrics().elidedText(panel.gpt.cache_metric.text(),ui.Qt.TextElideMode.ElideRight,panel.gpt.cache_metric.width())==panel.gpt.cache_metric.text()
    assert panel.gpt.status.palette().color(QPalette.ColorRole.WindowText).name()==ui.THEMES['light']['muted']
    assert panel.anthropic.status.palette().color(QPalette.ColorRole.WindowText).name()==ui.THEMES['light']['muted']
    assert panel.anthropic.progress.palette().color(QPalette.ColorRole.WindowText).name()==ui.THEMES['light']['accent']
    panel.grab().save(str(out/'panel-light.png'))
    panel.update_data(dict(route,verdict='OBSERVATION_GAP',served=None),claude)
    assert panel.gpt.served.text()=='모델명 미수집'
    assert panel.gpt.status.text()=='모델 대기'
    (out/'result.json').write_text(json.dumps(dict(passed=True,platform=sys.platform,backend=os.environ['QT_QPA_PLATFORM'],
       columns=columns,typography=typography,chevron_edge=arrow_edges[0],card=[320,307],inset=8,title_expansion=expanded,menus_have_equal_columns=True,
       current_selections=settings,native_popup_and_escape=True,menu_roles_separated=True,
       pet_menu_dismissal_preserves_pin=True,model_calls=0,native_windows=sys.platform=='win32'),ensure_ascii=False,indent=2)+'\n')
    for widget in (panel.detail_popup,panel.title_popup,panel.popup,pet,panel):widget.close()
