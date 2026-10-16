#!/usr/bin/env python3

import os, sys, logging, subprocess, threading
import gi
from cameractrls import CameraCtrls, ConfigPreset, PTZHWControllers, find_by_text_id, get_devices, v4ldirs, find_idx
from cameractrls import parse_hex_color, format_hex_color
from cameractrls import version, ghurl, find_device_holders, get_configfilename, usb_reset, wait_for_node
import cameraptzc60e
import emcheck
from emwatch import DarkWatchdog

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib, Pango, Gdk

PREVIEW_PANE_WIDTH = 440
PREVIEW_MISSING = ('Live preview needs GStreamer with v4l2src, jpegdec and gtk4paintablesink, and it was not found here. '
                   'The viewer button next to it still works.')

#logging.getLogger().setLevel(logging.INFO)

class CameraCtrlsWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.devices = []
        
        self.fd = 0
        self.device = None
        self.camera = None
        self.listener = None
        self.remote_watcher = None
        self.remote_timeout = None
        self.ptz_controllers = None
        self.preview = None
        self.preview_timer = None
        self.preview_loading = False
        self.remote_check_open = False

        self.grid = None
        self.frame = None
        self.device_dd = None

        self.zoom_absolute_sc = None
        self.pan_speed_sc = None
        self.tilt_speed_sc = None
        self.pan_absolute_sc = None
        self.tilt_absolute_sc = None

        self.init_window()
        self.refresh_devices()

    def init_window(self):
        css_provider = Gtk.CssProvider()
        css = '''
        .white-balance-temperature trough {
            background-image: linear-gradient(to right, 
                #89F3FF, #AFF7FF, #DDFCFF, #FFF2AA, #FFDD27, #FFC500, #FFB000, #FF8D00, #FF7A00
            );
        }
        .white-balance-temperature trough:disabled {
            background-blend-mode: color;
        }
        .dark-to-light trough {
            background-image: linear-gradient(to right, 
                #888888, #dddddd
            );
        }
        .dark-to-light trough:disabled {
            background-blend-mode: color;
        }
        /* make page stackswitcher gtk3 size */
        stackswitcher button {
            padding-left: 10px;
            padding-right: 10px;
        }
        .app-notification {
            background-color: @theme_bg_color;
            border: 1px solid alpha(currentColor, 0.2);
            border-radius: 8px;
            padding: 8px 12px;
            margin: 14px;
        }
        scale value {
            min-width: 4.5em;
        }
        .section-title {
            margin-top: 18px;
            margin-bottom: 8px;
        }
        .info-grid label.key {
            opacity: 0.65;
        }
        .footer-title {
            margin-bottom: 6px;
            opacity: 0.65;
            font-size: 0.85em;
            font-weight: bold;
        }
        .dim-row {
            opacity: 0.75;
        }
        .preview-picture {
            background: #000;
        }
        /* scale layout fix */
        scale trough {
            margin-top: 6px;
            margin-bottom: -6px;
        }
        '''

        gtk_version = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())

        if gtk_version >= (4, 12, 0):
            css_provider.load_from_string(css)
        # XXX: remove workaround when merged and pygobject 3.46 is widespread
        # https://gitlab.gnome.org/GNOME/pygobject/-/merge_requests/231/
        elif gtk_version >= (4, 9, 0):
            css_provider.load_from_data(css, -1)
        else:
            css_provider.load_from_data(css.encode())

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        try:
            self.desktop_portal = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                'org.freedesktop.portal.Settings', None)
            prefer_dark = self.desktop_portal.Read('(ss)', 'org.freedesktop.appearance', 'color-scheme') == 1
            self.get_settings().set_property('gtk-application-prefer-dark-theme', prefer_dark)
            self.desktop_portal.connect('g-signal', lambda p, sender, signal, params:
                signal == 'SettingChanged' and
                params[0] == 'org.freedesktop.appearance' and
                params[1] == 'color-scheme' and
                self.get_settings().set_property('gtk-application-prefer-dark-theme', params[2] == 1)
            )
        except Exception as e:
            logging.warning(f'desktop_portal failed: {e}')

        menu = Gio.Menu()
        menu.append('Shortcuts', 'app.shortcuts')
        menu.append('About', 'app.about')
        about_button = Gtk.MenuButton(
            menu_model=menu,
            icon_name='open-menu-symbolic',
            has_frame=False,
            tooltip_text='Main menu',
        )

        self.open_cam_button = Gtk.Button(
            action_name='app.open_camera_window',
            action_target=GLib.Variant('s', ''),
            icon_name='camera-video-symbolic', has_frame=False,
            tooltip_text='Show preview window',
        )

        self.ptz_model = Gtk.StringList()
        self.ptz_lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.ptz_lb.bind_model(self.ptz_model, lambda name: Gtk.ToggleButton(label=name.get_string()))

        self.pad_map_button = Gtk.Button(label='Button map', visible=False)
        self.pad_map_button.connect('clicked', lambda b: self.show_pad_map())
        ptz_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ptz_box.append(self.ptz_lb)
        ptz_box.append(self.pad_map_button)

        self.pad_label = Gtk.Label(visible=False)
        ptz_btn_box = Gtk.Box(spacing=6)
        ptz_btn_box.append(Gtk.Image(icon_name='input-gaming-symbolic'))
        ptz_btn_box.append(self.pad_label)
        self.ptz_sw = Gtk.MenuButton(
            popover=Gtk.Popover(position=Gtk.PositionType.BOTTOM, child=ptz_box),
            child=ptz_btn_box,
            has_frame=False,
            tooltip_text='PTZ hardware controls',
        )

        refresh_button = Gtk.Button(icon_name='view-refresh-symbolic', has_frame=False, tooltip_text='Refresh')
        refresh_button.connect('clicked', lambda e: self.refresh_devices())

        headerbar = Gtk.HeaderBar(show_title_buttons=True)
        headerbar.pack_start(refresh_button)
        self.remote_chip = Gtk.Box(valign=Gtk.Align.CENTER, spacing=6, visible=False)
        self.remote_label = Gtk.Label(label='Changed by remote')
        self.remote_chip.append(self.remote_label)
        resync_button = Gtk.Button(label='Re-sync', tooltip_text='Load the active named preset again so the app and the camera agree')
        resync_button.connect('clicked', lambda b: self.resync_remote())
        self.remote_chip.append(resync_button)
        headerbar.pack_start(self.remote_chip)
        self.dark_chip = Gtk.Box(valign=Gtk.Align.CENTER, spacing=6, visible=False)
        self.dark_chip.append(Gtk.Label(label='Camera went dark'))
        self.dark_fix = Gtk.Button(label='Fix', tooltip_text='Hand exposure back from the camera and load the active preset again')
        self.dark_fix.add_css_class('suggested-action')
        self.dark_fix.connect('clicked', lambda b: self.fix_dark())
        self.dark_chip.append(self.dark_fix)
        headerbar.pack_start(self.dark_chip)
        self.dark_watch = DarkWatchdog()
        self.dark_reset = Gtk.Button(label='Reset camera (USB)', visible=False,
            tooltip_text='Last resort: reset the camera on the USB bus and apply the active preset again')
        self.dark_reset.connect('clicked', lambda b: self.reset_usb())
        self.dark_chip.append(self.dark_reset)
        self.usb_resetting = False
        self.dark_seen = 0.0
        headerbar.pack_end(about_button)
        headerbar.pack_end(self.open_cam_button)
        self.preview_toggle = Gtk.ToggleButton(icon_name='video-display-symbolic', has_frame=False,
            tooltip_text='Live preview in this window', visible=False)
        self.preview_toggle.connect('toggled', lambda b: self.preview_toggled(b))
        headerbar.pack_end(self.preview_toggle)
        self.quick_box = Gtk.Box(valign=Gtk.Align.CENTER)
        self.quick_box.add_css_class('linked')
        self.quick_buttons = {}
        headerbar.pack_end(self.quick_box)
        headerbar.pack_end(self.ptz_sw)
        self.set_titlebar(headerbar)

        self.grid = Gtk.Grid()

        self.zero_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, halign=Gtk.Align.CENTER)
        self.zero_box.append(Gtk.Image(icon_name='camera-disabled-symbolic', icon_size=Gtk.IconSize.LARGE, pixel_size=96,
                                margin_top=30, margin_bottom=10, margin_start=80, margin_end=80))
        self.zero_box.append(Gtk.Label(label='<span size="17000" color="gray">No camera found</span>', use_markup=True,
                                margin_top=10, margin_bottom=6))
        self.zero_box.append(Gtk.Label(label='Plug it in, then try again.', margin_bottom=14))
        retry_button = Gtk.Button(label='Try again', halign=Gtk.Align.CENTER, margin_bottom=20)
        retry_button.add_css_class('suggested-action')
        retry_button.connect('clicked', lambda b: self.refresh_devices())
        self.zero_box.append(retry_button)
        if 'SNAP' in os.environ:
            self.zero_box.append(Gtk.Label(label='Please permit access with', max_width_chars=30, wrap=True, margin_top=10))
            self.zero_box.append(Gtk.Label(label='snap connect cameractrls:camera', selectable=True, margin_bottom=10))

        self.device_dd = Gtk.DropDown(
            model=Gtk.StringList(), hexpand=True, halign=Gtk.Align.START, show_arrow=False,
            tooltip_text='Select other camera',
        )
        # use the default factory as list_factory
        self.device_dd.set_list_factory(self.device_dd.get_factory())
        self.device_dd.get_first_child().set_has_frame(False)
        # then set the icon_factory as factory
        icon_factory = Gtk.SignalListItemFactory()
        icon_factory.connect('setup', lambda f, item: item.set_child(Gtk.Image(icon_name='view-more-symbolic')))
        self.device_dd.set_factory(icon_factory)

        self.device_dd.connect('notify::selected-item', lambda e, _: self.gui_open_device(self.device_dd.get_selected()))
        headerbar.pack_start(self.device_dd)

        self.grid.attach(self.zero_box, 0, 0, 1, 1)

        self._notify_timeout = None

        overlay = Gtk.Overlay()
        overlay.set_child(self.grid)

        # Notification overlay widget
        self._revealer = Gtk.Revealer(valign=Gtk.Align.END, halign=Gtk.Align.CENTER)
        notify_box = Gtk.Box(spacing=12)
        notify_box.add_css_class('app-notification')
        self._notify_label = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.CHAR, natural_wrap_mode=Gtk.NaturalWrapMode.NONE, halign=Gtk.Align.FILL)
        self._notify_action = Gtk.Button(valign=Gtk.Align.CENTER, visible=False)
        self._notify_action_handler = None
        self._notify_action.connect('clicked', self.run_notify_action)
        notify_box.append(self._notify_label)
        notify_box.append(self._notify_action)
        self._revealer.set_child(notify_box)
        overlay.add_overlay(self._revealer)

        self.preview_picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.preview_picture.add_css_class('preview-picture')
        self.preview_label = Gtk.Label(label='', xalign=0)
        self.preview_label.add_css_class('dim-label')
        self.preview_message = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, visible=False, xalign=0,
            max_width_chars=40, margin_top=12, margin_start=12, margin_end=12)
        self.preview_retry = Gtk.Button(label='Try again', halign=Gtk.Align.START, visible=False, margin_start=12)
        self.preview_retry.connect('clicked', lambda b: self.start_preview())
        self.preview_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False,
            margin_top=10, margin_bottom=10, margin_start=10, margin_end=0, width_request=PREVIEW_PANE_WIDTH)
        for w in (self.preview_picture, self.preview_message, self.preview_retry, self.preview_label):
            self.preview_pane.append(w)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.preview_pane, end_child=overlay,
            resize_start_child=True, resize_end_child=False, shrink_start_child=False, shrink_end_child=False)
        self.set_child(self.paned)

        self.connect('close-request', lambda w: self.close_device())

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.handle_shortcut_key)
        self.add_controller(keys)

    def handle_shortcut_key(self, controller, keyval, keycode, state):
        if isinstance(self.get_focus(), Gtk.Editable):
            return False
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_F5 and not mods:
            self.refresh_devices()
            return True
        if mods != Gdk.ModifierType.CONTROL_MASK or self.camera is None:
            return False
        if keyval in (Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3, Gdk.KEY_4):
            self.load_quick_preset(chr(keyval))
        elif keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.reset_image()
        elif keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.get_application().activate_action('open_camera_window', GLib.Variant('s', self.device.path))
        else:
            return False
        return True

    def reset_image(self):
        reset = find_by_text_id(self.camera.get_ctrls(), 'emeet_c60e_reset_image')
        if reset is None:
            self.notify('This camera has no image reset', 2)
            return
        self.update_ctrl(reset, 'reset')

    def refresh_devices(self):
        logging.info('refresh_devices')
        self.devices = get_devices(v4ldirs)

        if len(self.devices) == 0:
            self.close_device()
            self.init_gui_device()

        model = self.device_dd.get_model()
        model.splice(0, model.get_n_items(), [d.name for d in self.devices])

        if len(self.devices):
            idx = 0
            if self.device in self.devices:
                idx = self.devices.index(self.device)
            self.device_dd.set_selected(idx)

        if len(self.devices) == 0:
            self.zero_box.set_visible(True)
            self.device_dd.set_visible(False)
            self.open_cam_button.set_visible(False)
            self.ptz_sw.set_visible(False)
        else:
            self.zero_box.set_visible(False)
            self.device_dd.set_visible(True)
            self.open_cam_button.set_visible(True)

    def device_lost(self):
        if self.device is None:
            return
        if self.usb_resetting:
            self.close_device()
            return
        name = self.device.name.rsplit(' (', 1)[0]
        logging.info(f'device lost: {name}')
        self.close_device()
        self.refresh_devices()
        self.notify(f'{name} was unplugged', 8)

    def gui_open_device(self, id):
        logging.info('gui_open_device')
        # if the selection is empty
        if id == Gtk.INVALID_LIST_POSITION:
            return

        self.close_device()
        self.open_device(self.devices[id])
        self.init_gui_device()

    def reopen_device(self):
        opened_page = self.stack.get_visible_child_name()
        device = self.device
        self.close_device()
        self.open_device(device)
        self.init_gui_device()
        self.stack.set_visible_child_full(opened_page, Gtk.StackTransitionType.NONE)

    def open_device(self, device):
        logging.info(f'opening device: {device.path}')
        try:
            self.fd = os.open(device.path, os.O_RDWR, 0)
        except Exception as e:
            logging.error(f'os.open({device.path}, os.O_RDWR, 0) failed: {e}')

        self.camera = CameraCtrls(device.path, self.fd)
        self.listener = self.camera.subscribe_events(
            lambda c: GLib.idle_add(self.update_ctrl_value, c),
            lambda errs: GLib.idle_add(self.notify, '\n'.join(errs)),
            lambda: GLib.idle_add(self.device_lost),
        )
        self.device = device
        self.remote_watcher = self.camera.subscribe_remote(lambda: GLib.idle_add(self.remote_pushed))
        self.ptz_controllers = PTZHWControllers(self.device.path,
            lambda check, p, i: GLib.timeout_add(300, check, p, i),
            lambda err: self.notify(err),
            lambda i: self.ptz_lb.get_row_at_index(i).get_child().set_active(False),
        )
        self.ptz_model.splice(0, self.ptz_model.get_n_items(), self.ptz_controllers.get_names())
        for i in range(self.ptz_model.get_n_items()):
            row = self.ptz_lb.get_row_at_index(i)
            row.set_activatable(False)
            row.set_focusable(False)
            row.get_child().connect('toggled', lambda c, i=i: self.ptz_controllers.set_active(i, c.get_active()))
        self.ptz_sw.set_visible(self.camera.has_ptz())
        self.ptz_sw.set_sensitive(self.ptz_model.get_n_items() != 0 or self.is_c60e())
        self.update_pad_state()
        self.open_cam_button.set_action_target_value(GLib.Variant('s', self.device.path))

    def is_c60e(self):
        return self.camera is not None and bool(self.camera.emeet_c60e.get_ctrls())

    def update_pad_state(self):
        c60e = self.is_c60e()
        self.pad_map_button.set_visible(c60e)
        self.pad_label.set_visible(c60e)
        if not c60e:
            return
        pads = [n for n in self.ptz_controllers.get_names() if 'box' in n.lower() or 'pad' in n.lower() or 'controller' in n.lower()]
        if pads:
            name = cameraptzc60e.pad_label(pads[0].rsplit(':', 1)[0])
            self.pad_label.set_label(f'Pad: {name} connected')
        else:
            self.pad_label.set_label('Pad: not connected')

    def show_pad_map(self):
        ini = get_configfilename(self.device.path)
        rows = cameraptzc60e.map_rows(*cameraptzc60e.load_profile(cameraptzc60e.read_ini(ini)))
        grid = Gtk.Grid(column_spacing=24, row_spacing=6, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        for i, (control, what) in enumerate(rows):
            grid.attach(Gtk.Label(label=control, xalign=0), 0, i, 1, 1)
            grid.attach(Gtk.Label(label=what, xalign=0), 1, i, 1, 1)
        note = Gtk.Label(label=f'Rebind under [pad] in {ini}', xalign=0, margin_top=12, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=60, selectable=True)
        note.add_css_class('dim-label')
        grid.attach(note, 0, len(rows), 2, 1)
        win = Gtk.Window(title='Controller map', transient_for=self, modal=True, child=grid, resizable=False)
        esc = Gtk.EventControllerKey()
        esc.connect('key-pressed', lambda c, keyval, *_: keyval == Gdk.KEY_Escape and win.close())
        win.add_controller(esc)
        win.present()

    def close_device(self):
        if self.fd:
            logging.info('close_device')
            self.stop_preview()
            self.preview_toggle.set_visible(False)
            self.device = None
            self.camera = None
            self.listener.stop()
            self.listener = None
            if self.remote_watcher is not None:
                self.remote_watcher.stop()
                self.remote_watcher = None
            self.hide_remote_chip()
            self.ptz_controllers.terminate_all()
            self.ptz_controllers = None
            os.close(self.fd)
            self.fd = 0

    def init_info_grid(self, frame, cat):
        grid = Gtk.Grid(column_spacing=24, row_spacing=10, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        grid.add_css_class('info-grid')
        for row, c in enumerate(cat.ctrls):
            key = Gtk.Label(label=c.name, xalign=0, yalign=0)
            key.add_css_class('key')
            value = Gtk.Label(label=c.value, selectable=True, xalign=0, hexpand=True, wrap=True,
                wrap_mode=Pango.WrapMode.CHAR, natural_wrap_mode=Gtk.NaturalWrapMode.INHERIT, max_width_chars=48)
            if c.tooltip:
                key.set_tooltip_text(c.tooltip)
            grid.attach(key, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            c.gui_ctrls = [key]
            c.gui_value_set = value.set_label
            c.gui_default_btn = None
        frame.set_child(grid)

    def default_button(self):
        return Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER, halign=Gtk.Align.START, has_frame=False,
            tooltip_text='Back to the default')

    def init_quick_presets(self):
        while (child := self.quick_box.get_first_child()) is not None:
            self.quick_box.remove(child)
        self.quick_buttons = {}
        if self.camera is None:
            self.quick_box.set_visible(False)
            return
        for num, name in self.camera.config_preset.named_presets():
            b = Gtk.ToggleButton(label=name, tooltip_text=f'Load preset {num} ({name})')
            b.connect('clicked', lambda b, num=num: self.load_quick_preset(num))
            self.quick_box.append(b)
            self.quick_buttons[num] = b
        self.quick_box.set_visible(bool(self.quick_buttons))

    def load_quick_preset(self, num):
        preset = find_by_text_id(self.camera.get_ctrls(), 'preset')
        self.update_ctrl(preset, f'load_{num}')

    def update_quick_presets(self):
        if self.camera is None:
            return
        for num, b in self.quick_buttons.items():
            b.set_active(self.camera.config_preset.preset_matches(num))

    def init_gui_device(self):
        if self.frame:
            self.grid.remove(self.frame)
            self.frame = None

        self.init_quick_presets()

        if self.device is None:
            return

        self.frame = Gtk.Grid(hexpand=True, halign=Gtk.Align.FILL)
        self.grid.attach(self.frame, 0, 0, 1, 1)

        stack_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, halign=Gtk.Align.FILL,
            margin_bottom=10, margin_top=10, margin_start=10, margin_end=10)
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=500)
        stack_sw = Gtk.StackSwitcher(stack=stack, hexpand=True, halign=Gtk.Align.CENTER)
        stack_box.append(stack_sw)
        scrolledwindow = Gtk.ScrolledWindow(child=stack, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        stack_box.append(scrolledwindow)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=10)
        stack_box.append(footer)

        self.frame.attach(stack_box, 0, 1, 1, 1)
        self.stack = stack

        for page_n, page in enumerate(self.camera.get_ctrl_pages()):
            page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            if page.target == 'main':
                stack.add_titled(page_box, str(page_n), page.title)
            elif page.target == 'footer':
                sep = Gtk.Separator(margin_bottom=10)
                footer.append(sep)
                footer.append(page_box)

            for cat in page.categories:
                if page.target != 'footer':
                    c_label = Gtk.Label(xalign=0)
                    c_label.add_css_class('section-title')
                    c_label.set_markup(f'<b>{cat.title}</b>')
                    page_box.append(c_label)
                else:
                    is_presets = any(c.text_id == 'preset' for c in cat.ctrls)
                    f_label = Gtk.Label(label='Presets' if is_presets else cat.title, xalign=0, margin_start=2)
                    f_label.add_css_class('footer-title')
                    page_box.append(f_label)

                ctrls_frame = Gtk.Frame()
                page_box.append(ctrls_frame)

                if all(c.type == 'info' for c in cat.ctrls):
                    self.init_info_grid(ctrls_frame, cat)
                    continue

                ctrls_listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                ctrls_listbox.set_header_func(lambda row, before: row.set_header(Gtk.Separator()) if before is not None else None)
                ctrls_frame.set_child(ctrls_listbox)

                for c in cat.ctrls:
                    ctrl_row = Gtk.ListBoxRow()
                    ctrls_listbox.append(ctrl_row)

                    ctrl_box = Gtk.Box(margin_start=5, margin_end=5, height_request=45)
                    ctrl_row.set_child(ctrl_box)

                    label = Gtk.Label(label=c.name, xalign=0, margin_end=5)
                    tooltip_markup = f'<b>{c.text_id}</b>'
                    if c.kernel_id:
                        tooltip_markup += f'  <b>({c.kernel_id})</b>'
                    if c.tooltip:
                        tooltip_markup += f'\n\n{c.tooltip}'
                    label.set_tooltip_markup(tooltip_markup)
                    ctrl_box.append(label)

                    c.gui_ctrls = [label]
                    c.gui_value_set = None

                    if c.type == 'integer':
                        adjustment = Gtk.Adjustment(lower=c.min, upper=c.max, value=c.value, step_increment=1)
                        adjustment.connect('value-changed', lambda a,c=c: self.update_ctrl(c, a.get_value()))
                        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, halign=Gtk.Align.END,
                            digits=0, has_origin=False, draw_value=True, value_pos=Gtk.PositionType.LEFT, adjustment=adjustment, width_request=264)
                        if c.zeroer:
                            for controller in scale.observe_controllers():
                                if isinstance(controller, gi.repository.Gtk.GestureClick):
                                    controller.connect('released', lambda c, n, x, y, sc=scale: sc.set_value(0))
                                if isinstance(controller, gi.repository.Gtk.EventControllerKey):
                                    controller.connect('key-pressed', self.handle_ptz_speed_key_pressed)
                                    controller.connect('key-released', self.handle_ptz_speed_key_released)
                        if c.step and c.step != 1:
                            adjustment_step = Gtk.Adjustment(lower=c.min, upper=c.max, value=c.value, step_increment=c.step)
                            adjustment_step.connect('value-changed', lambda a,c=c,a1=adjustment: [a.set_value(a.get_value() - a.get_value() % c.step),a1.set_value(a.get_value())])
                            scale.set_adjustment(adjustment_step)
                        if c.step_big:
                            scale.get_adjustment().set_page_increment(c.step_big)
                        if c.scale_class:
                            scale.add_css_class(c.scale_class)
                        if c.format_value:
                            scale.set_format_value_func(c.format_value)
                            if c.text_id == 'zoom_absolute':
                                scale.set_size_request(340, -1)
                        if c.value_tooltip:
                            scale.set_tooltip_text(c.value_tooltip(c.value))
                            scale.get_adjustment().connect('value-changed', lambda a, c=c, sc=scale: sc.set_tooltip_text(c.value_tooltip(a.get_value())))

                        if c.default is not None:
                            scale.add_mark(value=c.default, position=Gtk.PositionType.BOTTOM, markup=None)

                        if c.text_id == 'zoom_absolute':
                            self.zoom_absolute_sc = scale

                        if c.text_id == 'pan_speed':
                            self.pan_speed_sc = scale

                        if c.text_id == 'tilt_speed':
                            self.tilt_speed_sc = scale

                        if c.text_id == 'pan_absolute':
                            self.pan_absolute_sc = scale

                        if c.text_id == 'tilt_absolute':
                            self.tilt_absolute_sc = scale

                        if c.text_id in ['pan_absolute', 'tilt_absolute']:
                            for controller in scale.observe_controllers():
                                if isinstance(controller, gi.repository.Gtk.EventControllerKey):
                                    controller.connect('key-pressed', self.handle_ptz_absolute_key_pressed)

                        refresh = self.default_button()
                        refresh.connect('clicked', lambda e, c=c, sc=scale: sc.get_adjustment().set_value(c.default))
                        ctrl_box.append(refresh)
                        scale_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=500)
                        scale_box = Gtk.Box(halign=Gtk.Align.END)
                        spin_box = Gtk.Box(halign=Gtk.Align.END)
                        prev_button = Gtk.Button(icon_name='go-previous-symbolic', has_frame=False, opacity=0.2)
                        prev_button.connect('clicked', lambda e, st=scale_stack, sc=scale_box: st.set_visible_child(sc))
                        next_button = Gtk.Button(icon_name='go-next-symbolic', has_frame=False, opacity=0.2)
                        next_button.connect('clicked', lambda e, st=scale_stack, sp=spin_box: st.set_visible_child(sp))
                        scale_box.append(scale)
                        scale_box.append(next_button)
                        spin = Gtk.SpinButton(adjustment=scale.get_adjustment())
                        spin_box.append(spin)
                        spin_box.append(prev_button)
                        scale_stack.add_child(scale_box)
                        scale_stack.add_child(spin_box)
                        ctrl_box.append(scale_stack)
                        c.gui_value_set = scale.set_value
                        c.gui_ctrls += [scale, spin, refresh]
                        c.gui_default_btn = refresh

                    elif c.type == 'boolean':
                        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=c.value, margin_end=5, hexpand=True, halign=Gtk.Align.END)
                        switch.connect('state-set', lambda w,state,c=c: self.update_ctrl(c, state))
                        refresh = self.default_button()
                        if c.default is not None:
                            refresh.connect('clicked', lambda e,switch=switch,c=c: switch.set_active(c.default))
                        ctrl_box.append(refresh)
                        ctrl_box.append(switch)
                        c.gui_value_set = switch.set_active
                        c.gui_ctrls += [switch, refresh]
                        c.gui_default_btn = refresh

                    elif c.type == 'button':
                        filtered_menu = [m for m in c.menu if m.value is not None and not m.gui_hidden]
                        children_per_line = len(filtered_menu) if c.text_id == 'emeet_remote_step' else 4
                        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=4, row_spacing=4, hexpand=True, halign=Gtk.Align.END)
                        refresh = self.default_button()
                        ctrl_box.append(refresh)
                        ctrl_box.append(grid)
                        for idx, m in enumerate(filtered_menu):
                            b = Gtk.Button(label=m.name, valign=Gtk.Align.CENTER)
                            b.connect('clicked', lambda e, c=c, m=m, b=b: self.button_clicked(c, m, b))
                            if getattr(c, 'hold_repeat', False):
                                hold = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
                                hold.connect('pressed', lambda g, n, x, y, c=c, m=m, b=b: self.hold_start(c, m, b))
                                hold.connect('stopped', lambda g, b=b: self.hold_stop(b))
                                hold.connect('released', lambda g, n, x, y, b=b: self.hold_stop(b))
                                b.add_controller(hold)
                            if c.child_tooltip:
                                b.set_tooltip_markup(c.child_tooltip)
                            if m.lp_text_id is not None:
                                lp = Gtk.GestureLongPress(propagation_phase=Gtk.PropagationPhase.CAPTURE)
                                lp.connect('pressed', lambda lp, x, y, c=c, m=m, b=b: [
                                    lp.set_state(Gtk.EventSequenceState.CLAIMED),
                                    self.edit_step_popover(m, b) if c.text_id == 'emeet_remote_step' else self.update_ctrl(c, m.lp_text_id)
                                ])
                                b.add_controller(lp)
                            if c.text_id == 'preset':
                                rc = Gtk.GestureClick(button=3)
                                rc.connect('pressed', lambda g, n, x, y, m=m, b=b: self.rename_preset_popover(m, b))
                                b.add_controller(rc)
                            grid.attach(b, idx % children_per_line, idx // children_per_line, 1, 1)
                            c.gui_ctrls += [b]
                        if c.default is not None:
                            refresh.connect('clicked', lambda e,c=c: self.update_ctrl(c, c.default))
                        c.gui_ctrls += [refresh]
                        c.gui_default_btn = refresh

                    elif c.type == 'color':
                        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=4, row_spacing=4, hexpand=True, halign=Gtk.Align.END)
                        refresh = Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER, halign=Gtk.Align.START, has_frame=False)
                        picker = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)

                        def set_picker(value, picker=picker):
                            rgb = parse_hex_color(value or '000000')
                            rgba = Gdk.RGBA()
                            rgba.red, rgba.green, rgba.blue, rgba.alpha = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1.0
                            picker.set_rgba(rgba)

                        def picked(button, _, c=c):
                            rgba = button.get_rgba()
                            self.update_ctrl(c, format_hex_color(round(v * 255) for v in (rgba.red, rgba.green, rgba.blue)))

                        def swatch(value, c=c):
                            self.update_ctrl(c, value)
                            set_picker(c.value)

                        set_picker(c.value)
                        picker.connect('notify::rgba', picked)
                        ctrl_box.append(refresh)
                        ctrl_box.append(grid)
                        children_per_line = 4
                        for idx, m in enumerate(c.menu):
                            b = Gtk.Button(label=m.name, valign=Gtk.Align.CENTER)
                            b.connect('clicked', lambda e, m=m, swatch=swatch: swatch(m.value))
                            grid.attach(b, idx % children_per_line, idx // children_per_line, 1, 1)
                            c.gui_ctrls += [b]
                        grid.attach(picker, len(c.menu) % children_per_line, len(c.menu) // children_per_line, 1, 1)
                        refresh.connect('clicked', lambda e, c=c, swatch=swatch: swatch(c.default))
                        c.gui_value_set = set_picker
                        c.gui_ctrls += [picker, refresh]
                        c.gui_default_btn = refresh

                    elif c.type == 'info':
                        label = Gtk.Label(label=c.value, selectable=True, justify=Gtk.Justification.RIGHT, hexpand=True,
                                            halign=Gtk.Align.END, wrap=True, wrap_mode=Pango.WrapMode.CHAR,
                                            max_width_chars=48, width_chars=32, xalign=1,
                                            natural_wrap_mode=Gtk.NaturalWrapMode.INHERIT)
                        ctrl_box.append(label)
                        c.gui_value_set = label.set_label
                        c.gui_default_btn = None

                    elif c.type == 'menu':
                        if len(c.menu) < 4 and not c.menu_dd:
                            box = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=True, halign=Gtk.Align.END)
                            box.add_css_class('linked')
                            rb = None
                            for m in c.menu:
                                rb = Gtk.ToggleButton(group=rb, label=m.name)
                                rb.set_active(m.text_id == c.value)
                                rb.connect('toggled', lambda b, c=c, m=m: self.update_ctrl(c, m.text_id) if b.get_active() else None)
                                box.append(rb)
                                m.gui_rb = rb

                            refresh = self.default_button()
                            if c.default is not None:
                                refresh.connect('clicked', lambda e,c=c: find_by_text_id(c.menu, c.default).gui_rb.set_active(True))
                            ctrl_box.append(refresh)
                            ctrl_box.append(box)
                            c.gui_value_set = lambda ctext, c=c: find_by_text_id(c.menu, ctext).gui_rb.set_active(True)
                            c.gui_ctrls += [m.gui_rb for m in c.menu] + [refresh]
                            c.gui_default_btn = refresh
                        else:
                            wb_dd = Gtk.DropDown(model=Gtk.StringList(), valign=Gtk.Align.CENTER, hexpand=True, halign=Gtk.Align.END)
                            for m in c.menu:
                                wb_dd.get_model().append(m.name)
                            if c.value:
                                idx = find_idx(c.menu, lambda m: m.text_id == c.value)
                                if idx is not None:
                                    wb_dd.set_selected(idx)
                                else:
                                    wb_dd.set_selected(Gtk.INVALID_LIST_POSITION)
                                    err = f'Control {c.text_id}: Can\'t find {c.value} in {[m.text_id for m in c.menu]}'
                                    logging.warning(err)
                                    self.notify(err)
                            wb_dd.connect('notify::selected', lambda e,_,c=c: e.get_selected() != Gtk.INVALID_LIST_POSITION and GLib.idle_add(self.update_ctrl, c, c.menu[e.get_selected()].text_id))
                            refresh = self.default_button()
                            if c.default is not None:
                                refresh.connect('clicked', lambda e,c=c,wb_dd=wb_dd: wb_dd.set_selected(find_idx(c.menu, lambda m: m.text_id == c.default)))
                            ctrl_box.append(refresh)
                            ctrl_box.append(wb_dd)
                            c.gui_value_set = lambda ctext, c=c, wb_dd=wb_dd: wb_dd.set_selected(
                                Gtk.INVALID_LIST_POSITION if (i := find_idx(c.menu, lambda m: m.text_id == ctext)) is None else i)
                            c.gui_ctrls += [wb_dd, refresh]
                            c.gui_default_btn = refresh

                    tip = c.tooltip or c.name
                    for w in c.gui_ctrls[1:]:
                        if w.get_tooltip_text() is None:
                            w.set_tooltip_text(tip)
                    if c.text_id == 'preset_include_format':
                        label.add_css_class('dim-row')

        self.update_ctrls_state()
        self.preview_toggle.set_visible(True)
        self.restore_preview_state()
        _, natsize = self.get_preferred_size()
        self.set_default_size(natsize.width, natsize.height)

    def ini_path(self):
        return get_configfilename(self.device.path)

    def restore_preview_state(self):
        on = emcheck.ini_get(self.ini_path(), 'ui', 'preview', 'off') == 'on'
        self.preview_loading = True
        self.preview_toggle.set_active(on)
        self.preview_loading = False
        self.preview_pane.set_visible(on)
        if on:
            self.start_preview()

    def preview_toggled(self, button):
        if self.preview_loading or self.device is None:
            return
        on = button.get_active()
        try:
            emcheck.ini_set(self.ini_path(), 'ui', {'preview': 'on' if on else 'off'})
        except OSError as e:
            logging.warning(f'can\'t remember the preview state: {e}')
        if on:
            self.preview_pane.set_visible(True)
            w, h = self.get_width(), self.get_height()
            if w and not self.is_maximized():
                self.set_default_size(w + PREVIEW_PANE_WIDTH, h)
            self.start_preview()
        else:
            self.stop_preview()
            self.preview_pane.set_visible(False)
            w, h = self.get_width(), self.get_height()
            if w and not self.is_maximized():
                self.set_default_size(max(w - PREVIEW_PANE_WIDTH, 1), h)

    def preview_say(self, text, retry=True):
        self.preview_message.set_label(text)
        self.preview_message.set_visible(bool(text))
        self.preview_retry.set_visible(bool(text) and retry)
        self.preview_label.set_label('')

    def start_preview(self):
        self.stop_preview()
        self.preview_picture.set_paintable(None)
        self.preview_say('')
        if self.camera is None:
            return
        fmt = self.camera.capture_format()
        if fmt is None:
            self.preview_say('Can\'t tell which capture format the camera is using, so there is no preview.', False)
            return
        try:
            import empreview
        except (ImportError, ValueError) as e:
            logging.info(f'preview: {e}')
            self.preview_say(PREVIEW_MISSING, False)
            return
        preview = empreview.Preview(self.device.path, fmt, self.preview_failed)
        try:
            paintable = preview.start()
        except empreview.PreviewBusy:
            self.preview_say(self.get_application().busy_message(self.device.path))
            return
        except empreview.PreviewUnavailable as e:
            logging.info(f'preview: {e}')
            self.preview_say(f'{PREVIEW_MISSING}\n({e})' if 'missing' in str(e) else str(e), False)
            return
        self.preview = preview
        self.preview_picture.set_paintable(paintable)
        self.preview_timer = GLib.timeout_add(500, self.preview_tick)

    def preview_tick(self):
        if self.preview is None:
            self.preview_timer = None
            return False
        size = self.preview.size()
        fps = self.preview.measure_fps()
        if self.preview.luma_at != self.dark_seen:
            self.dark_seen = self.preview.luma_at
            luma = self.preview.luma
            if self.dark_watch.feed(self.dark_seen, luma):
                self.dark_chip.set_visible(True)
            elif luma is not None and luma >= self.dark_watch.dark:
                self.dark_chip.set_visible(False)
        self.preview_label.set_label(f'{size[0]}x{size[1]}, {fps:.0f} fps' if size else 'starting...')
        return True

    def preview_failed(self, busy, message):
        self.stop_preview()
        if self.device is None:
            return
        if busy:
            self.preview_say(self.get_application().busy_message(self.device.path))
        else:
            self.preview_say(f'The preview stopped: {message}')

    def fix_dark(self):
        camera = self.camera
        if camera is None or not self.dark_fix.get_sensitive():
            return
        self.dark_fix.set_sensitive(False)
        preset = camera.config_preset
        values = preset.preset_values(preset.active_preset())
        mode = values.get('auto_exposure') or getattr(find_by_text_id(camera.get_ctrls(), 'auto_exposure'), 'value', None)
        extra = {k: values[k] for k in ('exposure_time_absolute', 'gain') if k in values}

        def work():
            errs = []
            if mode is not None:
                camera.reassert_exposure(mode, extra, errs)
            GLib.idle_add(self.fix_dark_done, errs)

        threading.Thread(target=work, daemon=True).start()

    def fix_dark_done(self, errs):
        self.dark_fix.set_sensitive(True)
        self.dark_chip.set_visible(False)
        self.dark_watch.reset()
        if self.camera is None:
            return False
        self.dark_reset.set_visible(bool(errs))
        if errs:
            self.notify('\n'.join(errs))
            self.dark_chip.set_visible(True)
        self.load_quick_preset(self.camera.config_preset.active_preset())
        if self.preview is not None:
            self.stop_preview()
            self.start_preview()
        return False

    def reset_usb(self):
        if self.camera is None or self.usb_resetting:
            return
        path = self.device.path
        self.stop_preview()
        ok, message = usb_reset(path)
        if not ok:
            self.notify(message, 12)
            return
        self.usb_resetting = True
        self.dark_chip.set_visible(False)
        self.notify('Resetting the camera...', 4)

        def wait():
            back = wait_for_node(path)
            GLib.idle_add(self.reset_usb_done, path, back)

        threading.Thread(target=wait, daemon=True).start()

    def reset_usb_done(self, path, back):
        self.usb_resetting = False
        self.refresh_devices()
        match = [i for i, d in enumerate(self.devices) if d.path == path]
        if not back or not match:
            self.notify('The camera did not come back after the reset. Unplug it and plug it in again.', 12)
            return False
        self.gui_open_device(match[0])
        self.load_quick_preset(self.camera.config_preset.active_preset())
        self.notify('The camera is back and the active preset is applied', 5)
        return False

    def stop_preview(self):
        """Lets go of the camera. The toggle keeps its state, so the preview can come back."""
        if self.preview_timer is not None:
            GLib.Source.remove(self.preview_timer)
            self.preview_timer = None
        self.dark_chip.set_visible(False)
        self.dark_watch.reset()
        preview, self.preview = self.preview, None
        if preview is not None:
            preview.stop()
            self.preview_picture.set_paintable(None)
            self.preview_label.set_label('')

    def show_remote_check(self):
        if self.remote_check_open or self.camera is None:
            return
        self.remote_check_open = True
        self.stop_preview()
        camera, device_path, ini = self.camera, self.device.path, self.ini_path()
        state = {'closed': False, 'running': False, 'cancel': False}

        prompt = Gtk.Label(wrap=True, xalign=0, label='Point the remote at the camera, set the seconds per button, then start. '
            'You will be asked to press each button in turn; the picture is read after each press.')
        prompt.add_css_class('title-3')
        progress = Gtk.ProgressBar(visible=False)
        wait_spin = Gtk.SpinButton.new_with_range(0, 15, 1)
        wait_spin.set_value(3)
        wait_row = Gtk.Box(spacing=8)
        wait_row.append(Gtk.Label(label='Seconds to wait after each cue'))
        wait_row.append(wait_spin)
        start = Gtk.Button(label='Start', halign=Gtk.Align.START)
        start.add_css_class('suggested-action')
        results_grid = Gtk.Grid(column_spacing=18, row_spacing=4, margin_top=6)
        summary_label = Gtk.Label(xalign=0, wrap=True, visible=False, margin_top=8)
        scroller = Gtk.ScrolledWindow(child=results_grid, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=16, margin_bottom=16, margin_start=18, margin_end=18)
        for w in (prompt, progress, wait_row, start, scroller, summary_label):
            box.append(w)
        win = Gtk.Window(title='Check remote', transient_for=self, modal=True, child=box, default_width=720, default_height=620)

        def finish(results, error):
            state['running'] = False
            start.set_sensitive(True)
            start.set_label('Run again')
            wait_spin.set_sensitive(True)
            progress.set_visible(False)
            if state['closed']:
                self.remote_check_done()
                return False
            if error:
                prompt.set_label(error)
            elif results:
                prompt.set_label('Done. Here is what the picture and the camera showed.')
                try:
                    emcheck.save_result(ini, results)
                except OSError as e:
                    logging.warning(f'can\'t save the remote check: {e}')
                info = find_by_text_id(camera.get_ctrls(), 'emeet_remote_check_info')
                if info is not None:
                    info.value = emcheck.last_result_text(ini)
                    if info.gui_value_set:
                        info.gui_value_set(info.value)
                self.fill_check_table(results_grid, results)
                summary_label.set_label(emcheck.summary(results))
                summary_label.set_visible(True)
            return False

        def say(i, total, name, phase):
            if phase == 'press':
                GLib.idle_add(lambda: (prompt.set_label(f'Press {name} on the remote now'), progress.set_fraction(i / total), False)[2])

        def work(wait):
            heard = emcheck.HeardCounter()
            watcher = camera.subscribe_remote(heard.push)
            grabber = None
            results, error = [], None
            try:
                fmt = camera.capture_format()
                if fmt is None:
                    raise emcheck.GrabError('Can\'t tell which capture format the camera is using.')
                grabber = emcheck.make_grabber(device_path, fmt)
                results = emcheck.run_check(grabber.grab, say, heard, wait=wait, cancelled=lambda: state['cancel'])
            except emcheck.GrabError as e:
                error = str(e)
                if 'busy' in error.lower():
                    error = self.get_application().busy_message(device_path)
            except Exception as e:
                logging.exception('remote check failed')
                error = f'The remote check stopped: {e}'
            finally:
                if grabber is not None:
                    grabber.close()
                if watcher is not None:
                    watcher.stop()
            if state['cancel']:
                results = []
            GLib.idle_add(finish, results, error)

        def begin(button):
            while (child := results_grid.get_first_child()) is not None:
                results_grid.remove(child)
            summary_label.set_visible(False)
            state['running'], state['cancel'] = True, False
            start.set_sensitive(False)
            wait_spin.set_sensitive(False)
            progress.set_fraction(0)
            progress.set_visible(True)
            prompt.set_label('Opening the camera...')
            threading.Thread(target=work, args=(float(wait_spin.get_value()),), daemon=True).start()

        def closing(w):
            state['closed'] = True
            if state['running']:
                state['cancel'] = True
            else:
                self.remote_check_done()
            return False

        start.connect('clicked', begin)
        win.connect('close-request', closing)
        win.present()

    def remote_check_done(self):
        self.remote_check_open = False
        if self.device is not None:
            self.restore_preview_state()

    def fill_check_table(self, grid, results):
        for col, head in enumerate(('Button', 'Seen', 'Camera heard', 'Verdict')):
            label = Gtk.Label(label=head, xalign=0)
            label.add_css_class('heading')
            grid.attach(label, col, 0, 1, 1)
        for row, r in enumerate(results, 1):
            grid.attach(Gtk.Label(label=r.name, xalign=0), 0, row, 1, 1)
            grid.attach(Gtk.Label(label=r.seen, xalign=0), 1, row, 1, 1)
            grid.attach(Gtk.Label(label='yes' if r.heard else 'no', xalign=0), 2, row, 1, 1)
            verdict = Gtk.Label(label=r.verdict, xalign=0, wrap=True, max_width_chars=44)
            verdict.add_css_class({'works': 'success', 'heard': 'warning', 'other': 'warning'}.get(r.status, 'error'))
            grid.attach(verdict, 3, row, 1, 1)

    def remote_pushed(self):
        if self.camera is None:
            return False
        changes = self.camera.v4l_ctrls.read_changes()
        names = ', '.join(f'{c.text_id.replace("_absolute", "").replace("_", " ")} {old} -> {new}' for c, old, new in changes[:3])
        self.remote_label.set_label(f'Remote: {names}' if names else 'Changed by remote')
        for c, _, _ in changes:
            if c.gui_value_set:
                c.gui_value_set(c.value)
            self.update_ctrl_state(c)
        self.remote_chip.set_visible(True)
        if self.remote_timeout is not None:
            GLib.Source.remove(self.remote_timeout)
        self.remote_timeout = GLib.timeout_add_seconds(10, self.remote_chip_expired)
        return False

    def remote_chip_expired(self):
        self.remote_timeout = None
        self.remote_chip.set_visible(False)
        return False

    def hide_remote_chip(self):
        if self.remote_timeout is not None:
            GLib.Source.remove(self.remote_timeout)
        self.remote_timeout = None
        self.remote_chip.set_visible(False)
        return False

    def resync_remote(self):
        self.hide_remote_chip()
        self.load_quick_preset(self.camera.config_preset.active_preset())

    def edit_step_popover(self, menu, button):
        sid = menu.lp_text_id.partition('_')[2]
        remote = self.camera.emeet_remote
        spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=remote.steps[sid], lower=0, upper=400, step_increment=1, page_increment=10),
            width_chars=5, tooltip_text=f'Zoom value for {sid.upper()}, 0 to 400')
        popover = Gtk.Popover(child=spin)
        popover.set_parent(button)
        popover.connect('closed', lambda p: GLib.idle_add(p.unparent))

        def apply(*_):
            errs = []
            spin.update()
            remote.set_step(sid, int(spin.get_value()), errs)
            popover.popdown()
            if errs:
                self.notify('\n'.join(errs))
            else:
                self.update_ctrl_value(find_by_text_id(self.camera.get_ctrls(), 'emeet_remote_steps_info'))
        spin.connect('activate', apply)
        popover.popup()
        spin.grab_focus()

    def rename_preset_popover(self, menu, button):
        num = menu.text_id.split('_')[1]
        entry = Gtk.Entry(text='' if menu.name == num else menu.name, max_length=ConfigPreset.MAX_NAME_LEN,
            placeholder_text=f'Preset {num}', width_chars=16)
        popover = Gtk.Popover(child=entry)
        popover.set_parent(button)
        popover.connect('closed', lambda p: GLib.idle_add(p.unparent))

        def rename(*_):
            errs = []
            self.camera.config_preset.rename_preset(num, entry.get_text(), errs)
            popover.popdown()
            if errs:
                self.notify('\n'.join(errs))
            else:
                GLib.idle_add(self.reopen_device)
        entry.connect('activate', rename)
        popover.popup()
        entry.grab_focus()

    def close_notify(self):
        self._revealer.set_reveal_child(False)
        self._notify_action_handler = None
        self._notify_timeout = None
        # False removes the timeout
        return False

    def run_notify_action(self, button):
        handler = self._notify_action_handler
        if self._notify_timeout is not None:
            GLib.Source.remove(self._notify_timeout)
        self.close_notify()
        if handler is not None:
            handler()

    def notify(self, message, timeout=5, action=None):
        if self._notify_timeout is not None:
            GLib.Source.remove(self._notify_timeout)
            self._notify_timeout = None

        self._notify_label.set_text(message)
        self._notify_action_handler = action[1] if action else None
        self._notify_action.set_visible(action is not None)
        if action:
            self._notify_action.set_label(action[0])
        self._revealer.set_reveal_child(True)

        if timeout > 0:
            self._notify_timeout = GLib.timeout_add_seconds(timeout, self.close_notify)

    HOLD_DELAY_MS = 450
    HOLD_TICK_MS = 90

    def button_clicked(self, ctrl, menu, button):
        if getattr(button, 'hold_fired', False):
            button.hold_fired = False
            return
        self.update_ctrl(ctrl, menu.text_id)

    def hold_start(self, ctrl, menu, button):
        self.hold_stop(button)
        button.hold_fired = False

        def tick():
            button.hold_fired = True
            self.update_ctrl(ctrl, menu.text_id)
            return True

        def begin():
            button.hold_source = GLib.timeout_add(self.HOLD_TICK_MS, tick)
            tick()
            return False

        button.hold_source = GLib.timeout_add(self.HOLD_DELAY_MS, begin)

    def hold_stop(self, button):
        source = getattr(button, 'hold_source', None)
        if source:
            GLib.source_remove(source)
            button.hold_source = None

    def update_ctrl(self, ctrl, value):
        if ctrl.text_id == 'emeet_remote_check':
            self.show_remote_check()
            return
        if ctrl.text_id == 'emeet_remote_usb_reset':
            self.reset_usb()
            return
        if ctrl.reopener:
            # a running preview holds the stream, the camera refuses a new format until it lets go
            self.stop_preview()
        # only update if out of sync (when new value comes from the gui)
        if ctrl.value != value and not ctrl.inactive:
            errs = []
            self.camera.setup_ctrls({ctrl.text_id: value}, errs)
            if errs:
                self.notify('\n'.join(errs))
                GLib.idle_add(self.update_ctrl_value, ctrl),
                return

        self.update_ctrls_state()
        for related in self.camera.related_ctrls(ctrl):
            self.update_ctrl_value(related)
        if ctrl.reopener:
            GLib.idle_add(self.reopen_device)

    def update_ctrls_state(self):
        for c in self.camera.get_ctrls():
            self.update_ctrl_state(c)
        self.update_quick_presets()

    def update_ctrl_state(self, c):
        for gui_ctrl in c.gui_ctrls:
            gui_ctrl.set_sensitive(not c.inactive and not c.readonly)
        if c.gui_default_btn is not None:
            visible = not c.inactive and not c.readonly and (
                c.default is not None and c.value is not None and c.default != c.value or \
                c.get_default is not None and not c.get_default()
            )
            c.gui_default_btn.set_opacity(visible)
            c.gui_default_btn.set_can_focus(visible)

    def update_ctrl_value(self, c):
        if c.reopener:
            self.reopen_device()
            return
        if c.gui_value_set:
            c.gui_value_set(c.value)
        self.update_ctrl_state(c)
        if hasattr(c, 'v4l2_id'):
            self.camera.v4l_ctrls.note_seen(c)
        for related in self.camera.related_ctrls(c):
            if related.gui_value_set:
                related.gui_value_set(related.value)
            self.update_ctrl_state(related)
        self.update_quick_presets()

    def handle_ptz_speed_key_pressed(self, c, keyval, keycode, state):
        pan_lower = self.pan_speed_sc.get_adjustment().get_lower()
        pan_upper =self.pan_speed_sc.get_adjustment().get_upper()
        tilt_lower = self.tilt_speed_sc.get_adjustment().get_lower()
        tilt_upper = self.tilt_speed_sc.get_adjustment().get_upper()

        if keyval in [Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_a]:
            self.pan_speed_sc.set_value(pan_lower)
        elif keyval in [Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_d]:
            self.pan_speed_sc.set_value(pan_upper)
        elif keyval in [Gdk.KEY_Up, Gdk.KEY_KP_Up, Gdk.KEY_w]:
            self.tilt_speed_sc.set_value(tilt_upper)
        elif keyval in [Gdk.KEY_Down, Gdk.KEY_KP_Down, Gdk.KEY_s]:
            self.tilt_speed_sc.set_value(tilt_lower)
        elif keyval == Gdk.KEY_KP_End:
            self.pan_speed_sc.set_value(pan_lower)
            self.tilt_speed_sc.set_value(tilt_lower)
        elif keyval == Gdk.KEY_KP_Page_Down:
            self.pan_speed_sc.set_value(pan_upper)
            self.tilt_speed_sc.set_value(tilt_lower)
        elif keyval == Gdk.KEY_KP_Home:
            self.pan_speed_sc.set_value(pan_lower)
            self.tilt_speed_sc.set_value(tilt_upper)
        elif keyval == Gdk.KEY_KP_Page_Up:
            self.pan_speed_sc.set_value(pan_upper)
            self.tilt_speed_sc.set_value(tilt_upper)
        elif self.zoom_absolute_sc is not None:
            return self.handle_ptz_key_pressed_zoom(keyval, state)
        else:
            return False
        return True

    def handle_ptz_absolute_key_pressed(self, c, keyval, keycode, state):
        pan_value = self.pan_absolute_sc.get_value()
        pan_step = self.pan_absolute_sc.get_adjustment().get_step_increment()
        tilt_value = self.tilt_absolute_sc.get_value()
        tilt_step = self.tilt_absolute_sc.get_adjustment().get_step_increment()
    
        if keyval in [Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_a]:
            self.pan_absolute_sc.set_value(pan_value - pan_step)
        elif keyval in [Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_d]:
            self.pan_absolute_sc.set_value(pan_value + pan_step)
        elif keyval in [Gdk.KEY_Up, Gdk.KEY_KP_Up, Gdk.KEY_w]:
            self.tilt_absolute_sc.set_value(tilt_value + tilt_step)
        elif keyval in [Gdk.KEY_Down, Gdk.KEY_KP_Down, Gdk.KEY_s]:
            self.tilt_absolute_sc.set_value(tilt_value - tilt_step)
        elif keyval == Gdk.KEY_KP_End:
            self.pan_absolute_sc.set_value(pan_value - pan_step)
            self.tilt_absolute_sc.set_value(tilt_value - tilt_step)
        elif keyval == Gdk.KEY_KP_Page_Down:
            self.pan_absolute_sc.set_value(pan_value + pan_step)
            self.tilt_absolute_sc.set_value(tilt_value - tilt_step)
        elif keyval == Gdk.KEY_KP_Home:
            self.pan_absolute_sc.set_value(pan_value - pan_step)
            self.tilt_absolute_sc.set_value(tilt_value + tilt_step)
        elif keyval == Gdk.KEY_KP_Page_Up:
            self.pan_absolute_sc.set_value(pan_value + pan_step)
            self.tilt_absolute_sc.set_value(tilt_value + tilt_step)
        elif keyval == Gdk.KEY_KP_Insert:
            self.pan_absolute_sc.set_value(0)
            self.tilt_absolute_sc.set_value(0)
        elif self.zoom_absolute_sc is not None:
            return self.handle_ptz_key_pressed_zoom(keyval, state)
        else:
            return False
        return True

    def handle_ptz_key_pressed_zoom(self, keyval, state):
        zoom_value = self.zoom_absolute_sc.get_value()
        zoom_step = self.zoom_absolute_sc.get_adjustment().get_step_increment()
        zoom_page = self.zoom_absolute_sc.get_adjustment().get_page_increment()
        zoom_lower = self.zoom_absolute_sc.get_adjustment().get_lower()
        zoom_upper = self.zoom_absolute_sc.get_adjustment().get_upper()

        if keyval in [Gdk.KEY_plus, Gdk.KEY_KP_Add] and state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_absolute_sc.set_value(zoom_value + zoom_page)
        elif keyval in [Gdk.KEY_minus, Gdk.KEY_KP_Subtract] and state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_absolute_sc.set_value(zoom_value - zoom_page)
        elif keyval in [Gdk.KEY_plus, Gdk.KEY_KP_Add]:
            self.zoom_absolute_sc.set_value(zoom_value + zoom_step)
        elif keyval in [Gdk.KEY_minus, Gdk.KEY_KP_Subtract]:
            self.zoom_absolute_sc.set_value(zoom_value - zoom_step)
        elif keyval in [Gdk.KEY_Home]:
            self.zoom_absolute_sc.set_value(zoom_lower)
        elif keyval in [Gdk.KEY_End]:
            self.zoom_absolute_sc.set_value(zoom_upper)
        elif keyval in [Gdk.KEY_Page_Up]:
            self.zoom_absolute_sc.set_value(zoom_value + zoom_page)
        elif keyval in [Gdk.KEY_Page_Down]:
            self.zoom_absolute_sc.set_value(zoom_value - zoom_page)
        else:
            return False
        return True

    def handle_ptz_speed_key_released(self, c, keyval, keycode, state):
        if keyval in [Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_KP_Left, Gdk.KEY_KP_Right, Gdk.KEY_a, Gdk.KEY_d]:
            self.pan_speed_sc.set_value(0)
        elif keyval in [Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_KP_Up, Gdk.KEY_KP_Down, Gdk.KEY_w, Gdk.KEY_s]:
            self.tilt_speed_sc.set_value(0)
        elif keyval in [Gdk.KEY_KP_End, Gdk.KEY_KP_Page_Down, Gdk.KEY_KP_Home, Gdk.KEY_KP_Page_Up]:
            self.pan_speed_sc.set_value(0)
            self.tilt_speed_sc.set_value(0)
        else:
            return False
        return True

class CameraCtrlsApp(Gtk.Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, application_id='hu.irl.cameractrls', **kwargs)

        self.window = None
        self.child_processes = []

    def do_startup(self):
        Gtk.Application.do_startup(self)

        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_theme.add_search_path(f'{sys.path[0]}/pkg')

        action = Gio.SimpleAction.new('about', None)
        action.connect('activate', self.on_about)
        self.add_action(action)

        action = Gio.SimpleAction.new('shortcuts', None)
        action.connect('activate', self.on_shortcuts)
        self.add_action(action)

        action = Gio.SimpleAction.new('open_camera_window', GLib.VariantType('s'))
        action.connect('activate', self.open_camera_window)
        self.add_action(action)

        action = Gio.SimpleAction.new('quit', None)
        action.connect('activate', self.on_quit)
        self.add_action(action)

        action = Gio.SimpleAction.new('alt_n', GLib.VariantType('i'))
        action.connect('activate', self.on_alt_n)
        self.add_action(action)

        self.set_accels_for_action('app.quit', ["<Primary>Q"])
        self.set_accels_for_action('app.alt_n(0)', ["<Alt>1"])
        self.set_accels_for_action('app.alt_n(1)', ["<Alt>2"])
        self.set_accels_for_action('app.alt_n(2)', ["<Alt>3"])
        self.set_accels_for_action('app.alt_n(3)', ["<Alt>4"])
        self.set_accels_for_action('app.alt_n(4)', ["<Alt>5"])
        self.set_accels_for_action('app.alt_n(5)', ["<Alt>6"])
        self.set_accels_for_action('app.alt_n(6)', ["<Alt>7"])

    def do_activate(self):
        self.window = CameraCtrlsWindow(application=self, title='Cameractrls')
        self.window.present()

    def on_about(self, action, param):
        about = Gtk.AboutDialog(transient_for=self.window, modal=self.window)
        about.set_program_name('Cameractrls')
        about.set_authors(['Gergo Koteles <soyer@irl.hu>'])
        about.set_artists(['Jorge Toledo https://lacajita.es'])
        about.set_copyright('Copyright © 2022 - 2024 Gergo Koteles')
        about.set_license_type(Gtk.License.LGPL_3_0)
        about.set_logo_icon_name('hu.irl.cameractrls')
        about.set_website(ghurl)
        about.set_website_label('GitHub')
        about.set_version(version)
        about.present()

    SHORTCUTS = [
        ('Pages', [('Alt+1 ... 5', 'Switch page')]),
        ('Presets', [('Ctrl+1 ... 4', 'Load preset 1 to 4'), ('Ctrl+R', 'Reset image')]),
        ('Window', [('Ctrl+V', 'Open the viewer'), ('F5', 'Refresh the camera list'), ('Ctrl+Q', 'Quit')]),
        ('In the viewer', [('F or double click', 'Fullscreen'), ('R', 'Rotate (Shift+R back)'), ('M', 'Mirror (Shift+M back)'),
                           ('C', 'Colormap (Shift+C back)'), ('Q or Esc', 'Close')]),
    ]

    def on_shortcuts(self, action, param):
        grid = Gtk.Grid(column_spacing=24, row_spacing=6, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        row = 0
        for title, items in self.SHORTCUTS:
            heading = Gtk.Label(xalign=0, margin_top=10 if row else 0)
            heading.set_markup(f'<b>{title}</b>')
            grid.attach(heading, 0, row, 2, 1)
            row += 1
            for keys, what in items:
                key_label = Gtk.Label(label=keys, xalign=0)
                key_label.add_css_class('monospace')
                grid.attach(key_label, 0, row, 1, 1)
                grid.attach(Gtk.Label(label=what, xalign=0), 1, row, 1, 1)
                row += 1
        win = Gtk.Window(title='Shortcuts', transient_for=self.window, modal=True, child=grid, resizable=False)
        esc = Gtk.EventControllerKey()
        esc.connect('key-pressed', lambda c, keyval, *_: keyval == Gdk.KEY_Escape and win.close())
        win.add_controller(esc)
        win.present()

    def on_quit(self, action, param):
        self.window.close()

    def on_alt_n(self, action, param):
        page_n = str(param)
        if self.window.stack and self.window.stack.get_child_by_name(page_n):
            self.window.stack.set_visible_child_name(page_n)

    def busy_message(self, device_path):
        holders = find_device_holders(device_path)
        if holders:
            who = ', '.join(f'{name} (pid {pid})' for pid, name in holders)
            return f'The camera is busy, {who} is using it. Close it there, then try again.'
        return 'The camera is busy, another program is using it. Close it there, then try again.'

    def check_preview_open(self, p, device_path):
        # if process returned
        if p.poll() is not None:
            (stdout, stderr) = p.communicate()
            errstr = stderr.decode()
            sys.stderr.write(errstr)
            if p.returncode != 0 and 'busy' in errstr.lower():
                self.window.notify(self.busy_message(device_path), 15,
                    ('Try again', lambda: self.open_camera_window(None, GLib.Variant('s', device_path))))
            elif p.returncode != 0:
                self.window.notify(errstr.strip())
            # False removes the timeout
            return False
        return True

    def open_camera_window(self, action, device):
        win_width, win_height = self.window.get_default_size()
        logging.info(f'open cameraview.py for {device.get_string()} with max size {win_width}x{win_height}')
        p = subprocess.Popen([f'{sys.path[0]}/cameraview.py', '-d', device.get_string(), '-s', f'{win_width}x{win_height}'], stderr=subprocess.PIPE)
        self.child_processes.append(p)
        GLib.timeout_add(300, self.check_preview_open, p, device.get_string())

    def kill_child_processes(self):
        running = [proc for proc in self.child_processes if proc.poll() is None]
        for proc in running:
            proc.terminate()
        for proc in running:
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                logging.warning(f'viewer {proc.pid} ignored SIGTERM, killing it')
                proc.kill()


if __name__ == '__main__':
    app = CameraCtrlsApp()
    app.run()
    app.kill_child_processes()
