"""Render only our own widgets in a disposable X server; no desktop/model access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
out, xvfb = map(lambda p: Path(p).resolve(), sys.argv[1:3])
out.mkdir(parents=True, exist_ok=True)
display = next(n for n in range(93, 110) if not Path(f"/tmp/.X11-unix/X{n}").exists())
server = subprocess.Popen([str(xvfb), f":{display}", "-screen", "0", "1280x800x24", "-nolisten", "tcp"], stdout=(out/'xvfb.log').open('w'), stderr=subprocess.STDOUT)
try:
    for _ in range(100):
        if Path(f"/tmp/.X11-unix/X{display}").exists():break
        if server.poll() is not None:raise RuntimeError('isolated X server exited')
        time.sleep(.02)
    os.environ['DISPLAY']=f':{display}'
    os.environ['CODEX_ROUTING_PET_CONFIG']=tempfile.mkdtemp(prefix='fluff-ui-test-')
    for key in ('GTK_PATH','GTK_IM_MODULE_FILE','GTK_EXE_PREFIX','GIO_MODULE_DIR'):
        os.environ.pop(key,None)
    from fluff_monitor import pet as ui
    ui.load_app_font()
    panel=ui.Panel({})
    panel.pinned=True
    view=ui.PetView(ui.sprites.load_pet(Path.home()/'.codex/pets/fluff'),120)
    pet=SimpleNamespace(view=view,visual_state='idle',frame_index=0)
    now=time.time()
    usage=dict(last=dict(input_tokens=10000,cached_input_tokens=9000,output_tokens=500,cached_fraction=.9),observed_at=now-3)
    route=dict(thread_id='parent',title='NBV 총괄',source='desktop',label='앱 실시간',active=True,connected=True,
               activity='running',updated_at=now,observed_at=now-3,verdict='ok',requested='gpt-6-astra',served='gpt-6-astra',effort='max',
               metrics_available=True,usage=usage,children=dict(running=2,details=[dict(name='검토',state='running')]),
               sessions=[dict(thread_id='parent',title='NBV 총괄',requested='gpt-6-astra',effort='max')],history_count=3)
    claude=dict(session_id='new',selected_session=None,label='응답 완료',state='completed',title='NBV 구현 담당',
                observed_at=now-2,requested_model='claude-opus-5-5',requested_effort='max',served='claude-opus-5-5',usage=usage,
                sessions=[dict(session_id='new',selection_key='role:implementation',title='NBV 구현 담당',process_alive=True)])
    def drain():
        end=time.monotonic()+.15
        while time.monotonic()<end:
            while ui.Gtk.events_pending():ui.Gtk.main_iteration_do(False)
            time.sleep(.005)
    panel.show_all();panel.update(route,claude);drain()
    stopped = dict(route, requested=None, served=None, configured_model='gpt-6-astra',
                   configured_effort='max', verdict='UNKNOWN', observation_disabled='observation_queue_limit',
                   detail='응답 모델 관측이 중단됐습니다. 사용량은 로컬 기록에서 읽습니다.')
    panel.update(stopped,claude);drain()
    assert panel.requested.caption_label.get_text() == '설정'
    assert panel.requested.get_text() == 'gpt-6-astra · max'
    assert panel.served.get_text() == '—'
    assert panel.chip.get_text() == '관측 중단'
    assert panel.cache.get_text()=='입력 캐시' and panel.cache.metric.get_text()=='90.0%'
    ui.capture_widgets(panel,pet,out/'capture-stopped.png')
    panel.update(route,claude);drain()
    assert panel.requested.caption_label.get_text() == '요청'
    # Exercise real GTK window placement, without moving windows on the user's desktop.
    workarea=SimpleNamespace(x=0,y=0,width=1280,height=800)
    pet.sprite_x,pet.sprite_y=600,300
    pet._workarea=lambda:workarea
    panel.pet=pet
    placement=[]
    for name,x,y,ax in [('middle',600,300,0),('left-edge',0,300,0),('top-edge',600,0,0),
                        ('bottom-edge',600,680,0),('second-monitor',-160,300,-1280)]:
        workarea.x=ax;pet.sprite_x,pet.sprite_y=x,y
        panel.place_near(pet,restore=False);drain()
        px,py=panel.get_position();pw,ph=panel.get_size()
        assert ax<=px<=ax+workarea.width-pw,(name,px)
        assert 0<=py<=workarea.height-ph,(name,py)
        if name in ('middle','left-edge','second-monitor'):
            assert abs((py+ph/2)-(y+pet.view.height/2))<=.5,(name,py,ph)
        assert (x-(px+pw)==12 or px-(x+pet.view.width)==12),(name,x,px,pw)
        placement.append(dict(case=name,position=[px,py]))
    workarea.x=0;pet.sprite_x,pet.sprite_y=600,300
    panel.place_near(pet,restore=False);drain()
    ui.capture_widgets(panel,pet,out/'panel.png')
    def color(widget, background=False):
        style=widget.get_style_context()
        value=(style.get_background_color if background else style.get_color)(ui.Gtk.StateFlags.NORMAL)
        return '#'+''.join(f'{round(v*255):02x}' for v in (value.red,value.green,value.blue))
    cache_colors={'dark':(color(panel.cache),color(panel.gpt_cache_button,True))}
    assert panel.get_size().width<=320,panel.get_size()
    assert panel.get_size().height==307,panel.get_size()
    size=list(panel.get_size())
    assert panel.gpt_mark.get_pixbuf().get_width()==16
    assert panel.claude_mark.get_pixbuf().get_width()==16
    assert panel.session_button.translate_coordinates(panel,0,0)[0]==panel.claude_button.translate_coordinates(panel,0,0)[0]
    assert not panel.requested.get_visible() and not panel.claude_requested.get_visible()
    value_column=[panel.session_title,panel.claude_title,panel.served,panel.claude_served,panel.cache,panel.claude_cache]
    value_x=[w.translate_coordinates(panel,0,0)[0] for w in value_column]
    assert value_x[0]==value_x[1] and value_x[2]==value_x[3] and value_x[4]==value_x[5],value_x
    assert value_x[0]==value_x[4]==panel.gpt_mark.translate_coordinates(panel,0,0)[0],value_x
    assert value_x[2]==panel.cache.metric.translate_coordinates(panel,0,0)[0],value_x
    assert value_x[3]==panel.claude_cache.metric.translate_coordinates(panel,0,0)[0],value_x
    typography={}
    for name,widget in [('title',panel.session_title),('model',panel.served),('metric',panel.cache.metric),('caption',panel.cache)]:
        font=widget.get_style_context().get_font(ui.Gtk.StateFlags.NORMAL)
        pixels=font.get_size()/ui.Pango.SCALE
        if not font.get_size_is_absolute():pixels*=widget.get_screen().get_resolution()/72
        typography[name]=dict(family=font.get_family(),pixels=round(pixels,2),weight=int(font.get_weight()),color=color(widget))
    assert [v['pixels'] for v in typography.values()]==[14,13,13,12],typography
    assert [v['weight'] for v in typography.values()]==[600,500,600,400],typography
    for widget in (panel.served,panel.claude_served):
        assert widget.create_pango_layout(widget.get_text()).get_pixel_size()[0]<=widget.get_allocated_width()
    arrow_edges=[image.translate_coordinates(panel,0,0)[0]+image.get_allocated_width() for image,_ in panel.chevrons]
    assert len(set(arrow_edges))==1,arrow_edges
    assert panel.cache.create_pango_layout(panel.cache.get_text()).get_pixel_size()[0] <= panel.cache.get_allocated_width()
    assert panel.session_title.translate_coordinates(panel.session_button,0,0)[0]==8
    assert panel.claude_title.translate_coordinates(panel.claude_button,0,0)[0]==8
    assert (panel.gpt_dot.get_allocated_width(),panel.gpt_dot.get_allocated_height())==(5,5)
    assert (panel.claude_dot.get_allocated_width(),panel.claude_dot.get_allocated_height())==(5,5)
    assert panel.cache.metric.get_text()=='90.0%'
    assert panel.claude_cache.metric.get_text()=='90.0%'
    assert panel.provider_context['gpt'][1].get_text()=='작업 중'
    assert panel.provider_context['claude'][1].get_text()=='응답 완료'
    assert panel.chip.get_text()=='모델 일치' and panel.claude.get_text()=='기록 확인'
    assert panel.pin_toggle.get_active() and panel.pin_toggle.get_tooltip_text()=='창 고정 해제'
    panel.pin_toggle.clicked();drain()
    assert not panel.pinned and panel.pin_toggle.get_tooltip_text()=='창 고정'
    panel.pin_toggle.clicked();drain()
    assert panel.pinned and panel.pin_toggle.get_tooltip_text()=='창 고정 해제'
    toolbar_buttons=[w for w in panel.pin_toggle.get_parent().get_children() if isinstance(w,ui.Gtk.Button)]
    assert set(toolbar_buttons)=={panel.pin_toggle,panel.theme_button}
    assert not hasattr(panel,'menu_button') and not hasattr(panel,'action_menu')
    panel.theme_button.clicked();drain()
    assert panel.settings['theme']=='light' and panel.pinned
    assert panel.theme_button.get_tooltip_text()=='어두운 화면으로 전환'
    panel.theme_button.clicked();drain()
    assert panel.settings['theme']=='dark' and panel.pinned
    assert panel.theme_button.get_tooltip_text()=='밝은 화면으로 전환'
    for provider in ('gpt','claude'):
        assert panel.provider_context[provider][0].get_text()=='자동 추적'
    panel.settings.update(codex_thread='parent',claude_session='role:implementation')
    panel.update(route,claude);drain()
    assert all(panel.provider_context[v][0].get_text()=='직접 선택' for v in ('gpt','claude'))
    ui.capture_widgets(panel,pet,out/'manual-selection.png')
    panel.settings.pop('codex_thread');panel.settings.pop('claude_session')
    panel.update(route,claude);drain()
    assert panel.cache.age.get_text().endswith('초 전')
    assert '새 출력 500 토큰' in panel.cache.get_tooltip_text()
    assert panel.claude_requested.get_text()=='claude-opus-5.5 · max'
    assert len(panel.claude_menu.choice_items)==2
    panel.update(route,dict(claude,session_id='next',sessions=[dict(session_id='next',selection_key='role:implementation',title='NBV 구현 담당',process_alive=True)]));drain()
    assert len(panel.claude_menu.choice_items)==2,'role rotation grew the menu'
    assert not any('이전 작업' in str(getattr(w,'get_label',lambda:'' )()) for w in panel.session_menu.items.get_children())
    panel.update(dict(route,usage=None),dict(claude,usage=None));drain()
    assert panel.cache.get_text()==panel.claude_cache.get_text()=='입력 캐시'
    assert panel.cache.metric.get_text()==panel.claude_cache.metric.get_text()=='기록 대기'
    assert panel.cache.metric.translate_coordinates(panel,0,0)[0]==value_x[2]
    ui.capture_widgets(panel,pet,out/'unknown.png')
    long_route=dict(route,title='아주 긴 한글 작업 이름과 very long English title without growing the panel',
        verdict='REROUTED',served='gpt-6-sol',historical_mismatches=1)
    panel.update(long_route,claude);drain()
    assert panel.get_size().width==size[0]
    assert panel.chip.get_text()=='모델 다름'
    assert panel.served.get_text()=='gpt-6-sol'
    assert abs(panel.get_position().root_y+panel.get_size().height/2-(pet.sprite_y+pet.view.height/2))<=.5
    ui.capture_widgets(panel,pet,out/'mismatch-long-title.png')
    # Only the hovered title strip extends; the entire card/grid must stay fixed.
    def settle(seconds=.65):
        end=time.monotonic()+seconds
        while time.monotonic()<end:drain()
    expanded=[]
    for button,title_widget,provider in [(panel.session_button,panel.session_title,'gpt'),
                                         (panel.claude_button,panel.claude_title,'claude')]:
        current=dict(route,title='Cross-Vendor AI 회의 보드 구현 · 최근 작업 상세')
        cc=dict(claude,title='Claude 구현 담당 · 도착 예측 검토 · 최근 작업 상세')
        panel.update(current,cc);drain()
        assert button.get_tooltip_text() is None
        before=[w.translate_coordinates(panel,0,0) for w in value_column]
        before_size=tuple(panel.get_size())
        panel._title_hover(button,True);settle()
        assert tuple(panel.get_size())==before_size,(provider,panel.get_size(),before_size)
        assert [w.translate_coordinates(panel,0,0) for w in value_column]==before
        reveal=panel.title_reveal
        assert reveal.get_visible()
        width=reveal.get_size().width
        assert button.get_allocated_width()<width<=420,(provider,width)
        natural=title_widget.create_pango_layout(title_widget.get_text()).get_pixel_size()[0]
        assert reveal.title.get_allocated_width()>=natural,(provider,reveal.title.get_allocated_width(),natural)
        assert reveal.get_size().height==button.get_allocated_height()
        bx,by=button.translate_coordinates(panel,0,0);px,py=panel.get_position()
        assert reveal.get_position().root_y==py+by
        rx,ry=reveal.get_position()
        assert not (rx < pet.sprite_x+pet.view.width and rx+width > pet.sprite_x
                    and ry < pet.sprite_y+pet.view.height and ry+reveal.get_size().height > pet.sprite_y)
        assert reveal.title.translate_coordinates(reveal,0,0)[0]==8
        for w in (panel.served,panel.claude_served):
            _,wy=w.translate_coordinates(panel,0,0)
            assert by+reveal.get_size().height<=wy or by>=wy+w.get_allocated_height()
        assert panel.session_button.get_allocated_width()==panel.claude_button.get_allocated_width()
        assert panel.session_button.get_allocated_height()==panel.claude_button.get_allocated_height()
        ui.capture_widgets(panel,pet,out/(provider+'-expanded.png'))
        expanded.append(dict(provider=provider,title_strip_width=width,title_width=natural,card_unchanged=before_size,rows_unchanged=True))
        # Crossing from the title button to its extended surface must not fold
        # an unpinned card while the pointer is still reading the title.
        if provider=='gpt':
            panel.pinned=False
            reveal._hover(True,SimpleNamespace(detail=ui.Gdk.NotifyType.NONLINEAR))
            panel._title_hover(button,False);panel.schedule_hide();settle()
            assert panel.get_visible() and reveal.get_visible()
            panel.pinned=True
            reveal._hover(False,SimpleNamespace(detail=ui.Gdk.NotifyType.NONLINEAR))
        panel._title_hover(button,False);settle()
        assert not reveal.get_visible()
        assert panel.get_size().width==320

    for provider, controls in [('gpt',(panel.gpt_cache_button,panel.gpt_status_button)),
                               ('claude',(panel.claude_cache_button,panel.claude_status_button))]:
        for kind, button in zip(('cache','status'),controls):
            before=tuple(panel.get_size())
            button.set_active(True);drain()
            popup=panel.detail_menu
            assert popup.get_visible(),(provider,kind)
            assert popup.get_size().width==320
            if kind=='cache':
                assert panel.cache_detail.get_visible() and not panel.detail_body.get_visible()
                assert panel.cache_detail.data['cached']==9000 and panel.cache_detail.data['other']==1000
                assert popup.get_size().height<=170,popup.get_size()
            else:
                assert not panel.cache_detail.get_visible() and panel.detail_body.get_visible()
                assert panel.detail_body.get_text()==button.detail_source.get_tooltip_text()
                assert panel.detail_body.get_allocated_height() >= panel.detail_body.get_layout().get_pixel_size()[1]
                assert popup.get_size().height<=140,popup.get_size()
            assert tuple(panel.get_size())==before
            import cairo
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,*popup.get_size())
            popup.draw(cairo.Context(surface))
            surface.write_to_png(str(out/(provider+'-'+kind+'-detail.png')))
            event=ui.Gdk.Event.new(ui.Gdk.EventType.KEY_PRESS);event.keyval=ui.Gdk.KEY_Escape
            popup.emit('key-press-event',event);drain()
            assert not popup.get_visible() and not button.get_active()

    panel.gpt_cache_button.set_active(True);drain()
    panel.update(dict(route,usage=None),claude);drain()
    assert not panel.cache_detail.data['known']
    assert panel.detail_menu.get_size().height<100,panel.detail_menu.get_size()
    panel.gpt_cache_button.set_active(False);drain()
    panel.chip.set_tooltip_text('새 응답의 모델명을 기다리고 있어요.')
    panel.gpt_status_button.set_active(True);drain()
    assert panel.detail_menu.get_size().height<90,panel.detail_menu.get_size()
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,*panel.detail_menu.get_size())
    panel.detail_menu.draw(cairo.Context(surface));surface.write_to_png(str(out/'short-status-detail.png'))
    panel.gpt_status_button.set_active(False);drain()
    panel.update(route,claude);drain()

    # Identical selector rows, both sides/corners, and no overlap with the sprite.
    choices=[]
    for name,x,y in [('middle',600,300),('left',0,300),('top-right',1160,0),('bottom-right',1160,680)]:
        pet.sprite_x,pet.sprite_y=x,y
        panel.update(route,claude);panel.place_near(pet,restore=False);drain()
        provider_columns=[]
        for button,menu,provider in [(panel.session_button,panel.session_menu,'gpt'),(panel.claude_button,panel.claude_menu,'claude')]:
            button.set_active(True);drain()
            mr=(*menu.get_position(),*menu.get_size());sr=(x,y,view.width,view.height)
            assert menu.get_visible(),(name,provider)
            assert not (mr[0]<sr[0]+sr[2] and mr[0]+mr[2]>sr[0] and mr[1]<sr[1]+sr[3] and mr[1]+mr[3]>sr[1]),(name,provider,mr,sr)
            rows=[]
            for key,item in menu.choice_items.items():
                marker,title=item.get_child().get_children()
                rows.append((marker.translate_coordinates(menu,0,0)[0],title.translate_coordinates(menu,0,0)[0],item.get_allocated_height()))
            assert len(set(rows))==1,(provider,rows)
            provider_columns.append(rows[0])
            if name=='middle':
                import cairo
                pr=(*panel.get_position(),*panel.get_size());rects=[mr,pr,sr]
                left=min(r[0] for r in rects)-8;top=min(r[1] for r in rects)-8
                surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,max(r[0]+r[2] for r in rects)-left+8,max(r[1]+r[3] for r in rects)-top+8)
                cr=cairo.Context(surface)
                for widget,rect in [(panel,pr),(menu,mr)]:
                    cr.save();cr.translate(rect[0]-left,rect[1]-top);widget.draw(cr);cr.restore()
                ui.Gdk.cairo_set_source_pixbuf(cr,view.frames('idle')[0],sr[0]-left,sr[1]-top);cr.paint()
                surface.write_to_png(str(out/(provider+'-selector.png')))
            choices.append(dict(case=name,provider=provider,menu_rect=mr,columns=rows[0]))
            button.set_active(False);drain()
        assert provider_columns[0]==provider_columns[1],provider_columns
    pet.sprite_x,pet.sprite_y=600,300
    panel.place_near(pet,restore=False);drain()
    panel.settings['theme']='light';panel.apply_theme();panel.update(route,claude);settle(.4)
    cache_colors['light']=(color(panel.cache),color(panel.gpt_cache_button,True))
    assert list(panel.get_size())==size,panel.get_size()
    assert panel.claude_menu.items.get_style_context().has_class('light')
    ui.capture_widgets(panel,pet,out/'light.png')
    # Contrast is a measured text check, not a claim of complete accessibility compliance.
    palettes={theme:{key:colors[key] for key in ('background','foreground','secondary','muted','accent','positive','warning','danger')}
              for theme,colors in ui.THEMES.items()}
    def luminance(hex_color):
        rgb=[int(hex_color[i:i+2],16)/255 for i in (1,3,5)]
        linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
        return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
    contrast={}
    for theme,colors in palettes.items():
        bg=luminance(colors['background']);contrast[theme]={}
        for key,color in colors.items():
            if key=='background':continue
            fg=luminance(color);ratio=(max(fg,bg)+.05)/(min(fg,bg)+.05)
            assert ratio>=4.5,(theme,key,ratio)
            contrast[theme][key]=round(ratio,3)
    for theme,(fg,bg) in cache_colors.items():
        a,b=luminance(fg),luminance(bg)
        ratio=(max(a,b)+.05)/(min(a,b)+.05)
        assert ratio>=4.5,(theme,'cache',fg,bg,ratio)
        contrast[theme]['rendered_cache']=round(ratio,3)
    # Separate information-window controls from temporary pet actions.
    actual_panel=ui.Panel({})
    settings=dict(ui.config.DEFAULTS,language='ko',walk=False,notifications=False,bubble='never',
                  throwing=False,usage=False,update_check=False,desktop=False)
    actual_pet=ui.Fluff(view,settings,actual_panel);actual_panel.pet=actual_pet
    actual_panel.update(route,claude)
    actual_panel.toggle_pin()
    assert not hasattr(actual_panel,'action_menu')
    menu_cases=[]
    def intersects(a,b):
        return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]
    for name,x,y in [('middle',600,300),('top-left',0,0),('top-right',1160,0),('bottom-left',0,680),('bottom-right',1160,680)]:
        actual_pet._place_sprite(x,y);actual_panel.reveal();drain()
        actual_pet._show_menu(SimpleNamespace(x_root=x+40,y_root=y+80));drain()
        menu=actual_pet.local_menu
        assert menu.get_visible(),name
        mr=(*menu.get_position(),*menu.get_size());pr=(*actual_panel.get_position(),*actual_panel.get_size())
        sr=(actual_pet.sprite_x,actual_pet.sprite_y,view.width,view.height)
        assert not intersects(mr,sr),(name,'pet',mr,sr)
        assert not actual_panel.get_visible() or not intersects(mr,pr),(name,'panel',mr,pr)
        assert 0<=mr[0] and 0<=mr[1] and mr[0]+mr[2]<=1280 and mr[1]+mr[3]<=800,(name,mr)
        actions=[w.get_label() for w in menu.items.get_children() if isinstance(w,ui.Gtk.Button)]
        assert actions==['쓰다듬기','산책 켜기','펫 종료'],actions
        assert all('고정' not in action and '화면' not in action for action in actions)
        menu_cases.append(dict(case=name,menu_rect=mr,pet_rect=sr,panel_rect=pr))
        if name=='middle':
            import cairo
            rects=[mr,pr,sr];left=min(a[0] for a in rects)-8;top=min(a[1] for a in rects)-8
            width=max(a[0]+a[2] for a in rects)-left+8;height=max(a[1]+a[3] for a in rects)-top+8
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height);cr=cairo.Context(surface)
            for widget,rect in [(actual_panel,pr),(menu,mr)]:
                cr.save();cr.translate(rect[0]-left,rect[1]-top);widget.draw(cr);cr.restore()
            ui.Gdk.cairo_set_source_pixbuf(cr,view.frames('idle')[0],sr[0]-left,sr[1]-top);cr.paint()
            surface.write_to_png(str(out/'context-menu.png'))
        assert menu._key_press(None,SimpleNamespace(keyval=ui.Gdk.KEY_Escape))
        assert not menu.get_visible()
        assert actual_panel.pinned

    actual_pet._place_sprite(600,300);actual_panel.reveal();drain()
    for selected in ('쓰다듬기','산책 켜기','산책 끄기'):
        actual_pet._show_menu(None);drain()
        menu=actual_pet.local_menu
        next(w for w in menu.items.get_children() if isinstance(w,ui.Gtk.Button) and w.get_label()==selected).clicked()
        drain()
        assert not menu.get_visible(),selected
        assert actual_panel.pinned and actual_panel.get_visible(),selected

    # Theme is one toolbar click; it closes temporary menus and preserves pinning.
    actual_pet._show_menu(None);drain()
    actual_panel.theme_button.clicked();drain()
    assert not actual_pet.local_menu.get_visible() and actual_panel.pinned
    assert actual_panel.settings['theme']=='light'
    actual_panel.theme_button.clicked();drain()
    assert actual_panel.settings['theme']=='dark' and actual_panel.pinned
    actual_panel.pin_toggle.clicked();drain()
    assert not actual_panel.pinned
    actual_panel.pin_toggle.clicked();drain()
    assert actual_panel.pinned
    actual_panel.session_button.set_active(True);drain()
    actual_pet._show_menu(None);drain()
    assert not actual_panel.session_menu.get_visible() and actual_pet.local_menu.get_visible()

    # Real focus transfer on the private display dismisses the temporary menu.
    menu=actual_pet.local_menu
    menu.get_window().focus(ui.Gdk.CURRENT_TIME);drain()
    outside=ui.Gtk.Window();outside.show_all();outside.get_window().focus(ui.Gdk.CURRENT_TIME);drain()
    assert not menu.get_visible()
    assert actual_panel.pinned and actual_panel.get_visible()
    outside.destroy()
    actual_pet.quit()

    # Exercise main() with an expired saved selection, not just a pure view helper.
    import sqlite3
    fixture=Path(tempfile.mkdtemp(prefix='fluff-runtime-test-'));state=fixture/'state';home=fixture/'codex';cfg=fixture/'pet'
    for folder in (state,home,cfg):folder.mkdir()
    with sqlite3.connect(home/'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT,title TEXT,name TEXT,source TEXT,archived INTEGER)')
        db.executemany('INSERT INTO threads VALUES(?,?,?,?,0)',[('old',None,'종료한 과거 작업','vscode'),('new',None,'현재 작업','vscode')])
    at=time.time()
    (state/'desktop.json').write_text(json.dumps(dict(active=True,updated_at=at,session_protocol=True,publisher_pid=os.getpid(),sessions=[
        dict(thread_id='new',requested='gpt-6-astra',served='gpt-6-astra',verdict='ok',response_id='fresh',status='completed',connected=True,request_started_at=at-5,observed_at=at-2),
        dict(thread_id='old',requested='gpt-6-astra',served='gpt-6-sol',verdict='ok',response_id='old',status='completed',connected=False,request_started_at=at-4000,observed_at=at-3999)])))
    (state/'activity.json').write_text(json.dumps(dict(updated_at=at,catalog_available=True,threads={
        'old':dict(title='종료한 과거 작업',kind='task',state='completed',state_at=at-3999),'new':dict(title='현재 작업',kind='task',state='running',state_at=at-1)},claude_sessions={
        'gone':dict(session_id='gone',state='offline',process_alive=False,state_at=at-1),
        'active':dict(session_id='active',title='현재 Claude',state='running',process_alive=True,state_at=at-1)})))
    (cfg/'panel.json').write_text(json.dumps(dict(codex_thread='old',claude_session='gone')))
    env=dict(os.environ,CODEX_HOME=str(home),CODEX_ROUTING_STATE_DIR=str(state),CODEX_ROUTING_PET_CONFIG=str(cfg))
    with (out/'runtime.log').open('w') as log:
        run=subprocess.run([sys.executable,'-B',str(ROOT/'run.py'),'pet','--test-seconds','2','--screenshot',str(out/'expired-selection.png')],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=8)
    assert run.returncode==0,(out/'runtime.log').read_text()
    saved=json.loads((cfg/'panel.json').read_text())
    assert 'codex_thread' not in saved and 'claude_session' not in saved,saved
    assert json.loads((state/'desktop.json').read_text())['sessions'][1]['thread_id']=='old','native history was mutated'
    (out/'result.json').write_text(json.dumps(dict(passed=True,width=panel.get_size().width,height=panel.get_size().height,
        role_rotation_menu_count=2,model_calls=0,isolated_display=True,contrast=contrast,typography=typography,value_column_x=value_x[2],label_column_x=value_x[0],chevron_edge=arrow_edges[0],status_dot_pixels=5,
        panel_placement=placement,vertical_alignment='fixed_sprite_center',gap_pixels=12,
        context_menu_cases=menu_cases,menu_roles_separated=True,pet_menu_dismissal_preserves_pin=True,
        outside_focus_dismisses_pet_menu=True,selector_cases=choices,inline_expansion=expanded,expired_saved_selections_cleared_in_main=True,
        verified=['dark','light','long_title','mismatch','missing_usage','role_rotation','provider_alignment','input_cache_semantics']),indent=2)+'\n')
    panel.destroy()
finally:
    server.terminate()
    server.wait(timeout=5)
