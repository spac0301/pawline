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
    route=dict(title='Cross-Vendor AI 회의 보드 구현',requested='gpt-6-astra',effort='max',served='gpt-6-astra',verdict='ok',usage=usage,
               sessions=[dict(thread_id='task-a',title='첫 번째 작업'),dict(thread_id='task-b',title='두 번째 작업')])
    claude=dict(title='Claude 구현 담당 · 저장 자료 검토',requested_model='claude-opus-5-5',requested_effort='max',served='claude-opus-5-5',label='작업 중',state='running',usage=usage,
                sessions=[dict(session_id='current',selection_key='role:implementation',title='구현 담당')])
    panel.update_data(route,claude);drain()
    assert (panel.width(),panel.height())==(288,201)
    widgets=[panel.gpt.title.caption,panel.gpt.requested,panel.gpt.served,panel.gpt.cache,panel.anthropic.title.caption,panel.anthropic.requested,panel.anthropic.served,panel.anthropic.cache]
    columns=[w.mapTo(panel,ui.QPoint(0,0)).x() for w in widgets]
    assert columns==[94]*8,columns
    assert panel.gpt.title.caption.x()==8 and panel.anthropic.title.caption.x()==8
    assert panel.anthropic.requested.text() == 'claude-opus-5.5 · max'
    assert panel.anthropic.served.text() == 'claude-opus-5.5'
    assert claude['served'] == 'claude-opus-5-5'
    assert panel.gpt.cache.text().startswith('입력 캐시 96.8%')
    assert panel.gpt.cache.text()==panel.anthropic.cache.text()
    assert '전체 입력' in panel.gpt.cache.toolTip()
    panel.grab().save(str(out/'panel-dark.png'))
    expanded=[]
    for name,anchor in [('gpt',panel.gpt.title),('claude',panel.anthropic.title)]:
        before=[w.mapTo(panel,ui.QPoint(0,0)) for w in widgets]
        panel.title_popup.reveal(anchor);drain(.3)
        assert panel.title_popup.isVisible(),name
        assert panel.title_popup.width()>anchor.width(),(name,panel.title_popup.width(),anchor.width())
        assert panel.title_popup.height()==24
        assert (panel.width(),panel.height())==(288,201)
        assert [w.mapTo(panel,ui.QPoint(0,0)) for w in widgets]==before
        assert panel.title_popup.button.caption.x()==8
        assert not panel.title_popup.geometry().intersects(ui.QRect(*panel.pet_rect()))
        panel.title_popup.grab().save(str(out/(name+'-title.png')))
        expanded.append(dict(provider=name,title_width=panel.title_popup.width(),card=[288,201]))
        panel.title_popup.hide()
    for provider, anchor in [('gpt', panel.gpt.status), ('claude', panel.anthropic.status)]:
        anchor.setText('활동 확인 중')
        before = [w.mapTo(panel, ui.QPoint(0, 0)) for w in widgets]
        anchor.hovered.emit(True); drain(.4)
        popup = panel.title_popup
        assert popup.isVisible(), provider
        assert popup.button.caption.text() == '활동 확인 중'
        assert not popup.button.arrow.isVisible()
        assert popup.button.caption.width() >= anchor.fontMetrics().horizontalAdvance(anchor.text())
        assert (panel.width(), panel.height()) == (288, 201)
        assert [w.mapTo(panel, ui.QPoint(0, 0)) for w in widgets] == before
        assert not popup.geometry().intersects(ui.QRect(*panel.pet_rect()))
        popup.grab().save(str(out/(provider+'-status.png')))
        anchor.hovered.emit(False); drain(.3)
        assert not popup.isVisible()
        anchor.setText('완료'); anchor.hovered.emit(True); drain(.3)
        assert not popup.isVisible(), 'unclipped status should stay inline'
        anchor.hovered.emit(False)
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
    panel.choose_provider('gpt');drain()
    assert app.activePopupWidget() is panel.popup
    QTest.keyClick(panel.popup,ui.Qt.Key.Key_Escape);drain()
    assert not panel.popup.isVisible()
    panel.actions('panel');drain()
    panel_actions=[b.accessibleName() for b in panel.popup.entries]
    assert panel_actions==['밝은 화면','정보창 고정 해제'],panel_actions
    panel.popup.grab().save(str(out/'panel-actions.png'))
    panel.popup.entries[1].click();drain()
    assert not panel.pinned and not panel.popup.isVisible()
    panel.actions('panel');panel.popup.entries[1].click();drain()
    assert panel.pinned
    for index in (0,1,1):
        panel.actions('pet');drain()
        actions=[b.accessibleName() for b in panel.popup.entries]
        assert actions==['쓰다듬기','산책 끄기' if pet.walking else '산책 켜기','펫 종료'],actions
        assert [b for b in panel.popup.findChildren(ui.QPushButton) if b.isVisible()]==panel.popup.entries
        assert not set(actions)&set(panel_actions)
        assert not panel.popup.geometry().intersects(pet.geometry())
        assert not panel.popup.geometry().intersects(panel.geometry())
        panel.popup.grab().save(str(out/'pet-actions.png'))
        panel.popup.entries[index].click();drain()
        assert not panel.popup.isVisible() and panel.pinned and panel.isVisible()
    panel.actions('pet');drain()
    QTest.keyClick(panel.popup,ui.Qt.Key.Key_Escape);drain()
    assert not panel.popup.isVisible() and panel.pinned
    panel.actions('pet');panel.actions('panel');drain()
    assert [b.accessibleName() for b in panel.popup.entries]==panel_actions
    panel.choose_provider('gpt');drain()
    assert panel.popup.width()==250
    panel.popup.hide()
    settings['theme']='light';panel.apply_theme();drain()
    panel.grab().save(str(out/'panel-light.png'))
    panel.update_data(dict(route,verdict='OBSERVATION_GAP',served=None),claude)
    assert panel.gpt.served.text()=='모델명 미수집'
    assert panel.gpt.status.text()=='관측 대기'
    (out/'result.json').write_text(json.dumps(dict(passed=True,platform=sys.platform,backend=os.environ['QT_QPA_PLATFORM'],
       columns=columns,card=[288,201],inset=8,title_expansion=expanded,menus_have_equal_columns=True,
       current_selections=settings,native_popup_and_escape=True,menu_roles_separated=True,
       pet_menu_dismissal_preserves_pin=True,model_calls=0,native_windows=sys.platform=='win32'),ensure_ascii=False,indent=2)+'\n')
    for widget in (panel.title_popup,panel.popup,pet,panel):widget.close()
