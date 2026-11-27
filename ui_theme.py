"""收发窗口共用的 ttk 主题、工具栏及显式保存的用户设置."""

import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font, messagebox, ttk

from transfer_state import atomic_write


PALETTES = {
    'light': {
        'bg': '#F7F8FA', 'fg': '#20252B', 'muted': '#465363',
        'field': '#FFFFFF', 'border': '#87939E', 'hover': '#E4EAF1',
        'accent': '#1D4ED8', 'accent_fg': '#FFFFFF', 'pressed': '#193EAA',
        'green': '#137354', 'yellow': '#956500', 'red': '#B54243', 'gray': '#78838C',
    },
    'dark': {
        'bg': '#171B21', 'fg': '#F0F3F7', 'muted': '#B5C1D0',
        'field': '#242B33', 'border': '#74808B', 'hover': '#35414C',
        'accent': '#89B4FF', 'accent_fg': '#121820', 'pressed': '#A7C8FF',
        'green': '#56CFA1', 'yellow': '#E5B84F', 'red': '#FF8B89', 'gray': '#87949F',
    },
}


def _settings_path(section):
    if os.name == 'nt':
        parent = Path(os.environ['APPDATA'])
    else:
        parent = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return parent / 'QRCodeTransceiver' / (section + '.json')


def load_defaults(section):
    """读取本机明确保存的一组参数; 首次使用时由各端提供内置默认值."""
    path = _settings_path(section)
    try:
        values = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise ValueError(f'无法读取设置文件 {path}: {exc}') from exc
    if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
        raise ValueError(f'设置文件应为字符串键值表: {path}')
    return values


def save_defaults(section, values):
    """复用现场文件的原子替换, 一次保存一组完整参数."""
    atomic_write(_settings_path(section), json.dumps(values, ensure_ascii=False, indent=2).encode('utf-8'))


def _color_caption(window, colors):
    """Windows 11 的非客户区由 DWM 单独绘制, CAPTION_COLOR/TEXT_COLOR 接受 COLORREF."""
    import ctypes
    handle = ctypes.c_void_p(int(window.frame(), 0))
    for attribute, key in ((35, 'bg'), (36, 'fg')):
        value = ctypes.c_uint32(int.from_bytes(bytes.fromhex(colors[key][1:]), 'little'))
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, attribute, ctypes.byref(value), 4)
        if result < 0:
            raise OSError(f'窗口标题栏配色错误: HRESULT {result:#x}')


class Theme:
    """为同一 Tk 解释器中的控件应用统一配色, 通知自绘组件重画."""

    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        app = root._root()
        if not hasattr(app, '_qr_appearance'):
            default = load_defaults('appearance').get('theme', 'light')
            if default not in PALETTES:
                raise ValueError(f'未知的窗口主题: {default}')
            app._qr_appearance = tk.StringVar(master=app, value=default)

            # 已经展开过的组合框会复用原 Listbox, 每次显示时同步其经典 Tk 配色.
            def color_list(event):
                colors = PALETTES[app._qr_appearance.get()]
                app.tk.call(str(event.widget), 'configure', '-background', colors['field'],
                            '-foreground', colors['fg'], '-selectbackground', colors['accent'],
                            '-selectforeground', colors['accent_fg'])
            app.bind_class('ComboboxListbox', '<Map>', color_list, add='+')
            if sys.platform == 'win32' and sys.getwindowsversion().build >= 22000:
                def mapped(event):
                    if isinstance(event.widget, (tk.Tk, tk.Toplevel)) and not event.widget.overrideredirect():
                        _color_caption(event.widget, PALETTES[app._qr_appearance.get()])
                app.bind_all('<Map>', mapped, add='+')
        self._choice = app._qr_appearance
        self._callbacks = []
        self._menus = []
        self._label = tk.StringVar(master=root)
        if self.style.theme_use() != 'clam':
            self.style.theme_use('clam')
        trace = self._choice.trace_add('write', self._refresh)

        def release(event):
            if event.widget is root:
                self._choice.trace_remove('write', trace)
        root.bind('<Destroy>', release, add='+')
        self._refresh()

    @property
    def name(self):
        return self._choice.get()

    @property
    def colors(self):
        return PALETTES[self.name]

    def on_change(self, callback):
        self._callbacks.append(callback)

    def set(self, name):
        """立即切换本会话主题."""
        if name not in PALETTES:
            raise ValueError(f'未知的窗口主题: {name}')
        self._choice.set(name)

    def _choose(self, name):
        self.set(name)
        try:
            save_defaults('appearance', {'theme': name})
        except OSError as exc:
            messagebox.showerror('主题偏好未保存', str(exc), parent=self.root)

    def _refresh(self, *_):
        c = self.colors
        self.root.configure(background=c['bg'])
        if sys.platform == 'win32' and sys.getwindowsversion().build >= 22000:
            for path in self.root.tk.splitlist(self.root.tk.call('wm', 'stackorder', self.root)):
                # Tk 的下拉列表是 Tcl 创建的弹出窗口, 只给应用自己的顶层窗口着色.
                if self.root.tk.call('winfo', 'class', path) in ('Tk', 'Toplevel'):
                    window = self.root.nametowidget(path)
                    if not window.overrideredirect():
                        _color_caption(window, c)
        self._label.set('亮色' if self.name == 'light' else '暗色')
        s = self.style
        s.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['field'],
                    troughcolor=c['hover'], bordercolor=c['border'], darkcolor=c['border'],
                    lightcolor=c['bg'], focuscolor=c['accent'], arrowcolor=c['fg'],
                    selectbackground=c['accent'], selectforeground=c['accent_fg'])
        s.configure('Muted.TLabel', foreground=c['muted'])
        title = font.nametofont('TkHeadingFont', root=self.root).actual()
        s.configure('Title.TLabel', font=(title['family'], abs(title['size']) + 4, 'bold'))
        for name in ('TButton', 'TMenubutton', 'TCheckbutton', 'TRadiobutton'):
            s.configure(name, padding=(7, 4))
            s.map(name, background=[('disabled', c['bg']), ('pressed', c['hover']), ('active', c['hover'])],
                  foreground=[('disabled', c['muted']), ('!disabled', c['fg'])])
        s.configure('Primary.TButton', foreground=c['accent_fg'], background=c['accent'],
                    bordercolor=c['accent'], lightcolor=c['accent'], darkcolor=c['accent'])
        s.map('Primary.TButton', background=[('disabled', c['hover']), ('pressed', c['pressed']),
                                             ('active', c['pressed']), ('!disabled', c['accent'])],
              foreground=[('disabled', c['muted']), ('!disabled', c['accent_fg'])])
        for name in ('TEntry', 'TCombobox', 'TSpinbox'):
            s.configure(name, insertcolor=c['fg'], padding=4)
            s.map(name, fieldbackground=[('disabled', c['bg']), ('readonly', c['field'])],
                  foreground=[('disabled', c['muted']), ('!disabled', c['fg'])],
                  bordercolor=[('focus', c['accent'])],
                  lightcolor=[('focus', c['accent'])], darkcolor=[('focus', c['accent'])])
        s.configure('Horizontal.TProgressbar', background=c['accent'], lightcolor=c['accent'],
                    darkcolor=c['accent'], troughcolor=c['hover'])
        s.map('Vertical.TScrollbar', background=[('active', c['hover'])])
        s.map('TCheckbutton', indicatorbackground=[('selected', c['accent']), ('!selected', c['field'])],
              indicatorforeground=[('selected', c['accent_fg'])])
        for menu in self._menus:
            menu.configure(background=c['field'], foreground=c['fg'], activebackground=c['accent'],
                           activeforeground=c['accent_fg'], disabledforeground=c['muted'])
        for callback in self._callbacks:
            callback()

    def toolbar(self, parent, tasks, settings):
        """构建两端共用的任务、参数设置和主题入口."""
        bar = ttk.Frame(parent, padding=(10, 3))
        task_button = ttk.Menubutton(bar, text='任务')
        task_menu = tk.Menu(task_button, tearoff=False)
        for label, command in tasks:
            task_menu.add_command(label=label, command=command)
        task_button.configure(menu=task_menu)
        task_button.pack(side='left')
        ttk.Button(bar, text='参数设置', command=settings).pack(side='left', padx=5)
        appearance = ttk.Menubutton(bar, textvariable=self._label)
        menu = tk.Menu(appearance, tearoff=False)
        for label, name in [('亮色', 'light'), ('暗色', 'dark')]:
            menu.add_command(label=label, command=lambda name=name: self._choose(name))
        appearance.configure(menu=menu)
        appearance.pack(side='right')
        self._menus.extend((task_menu, menu))
        self._refresh()
        return bar
