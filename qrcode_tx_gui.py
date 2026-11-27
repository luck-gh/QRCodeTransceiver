"""冻结发送任务, 校验定向补传请求, 循环播放二维码."""

import math
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from qr_render import ERROR_CORRECTION_LEVELS, build_qr
from repair_request import ranges
from transfer_protocol import MAX_CHUNK_SIZE, SOURCE
from transfer_state import SendTask
from ui_theme import Theme, load_defaults, save_defaults


DEFAULTS = {
    'chunk_size': '256', 'compress': '1', 'interval': '150', 'size': '600',
    'correction': 'M', 'border': '4', 'extra': '0.5',
}
PLAYBACK_FIELDS = (
    ('interval', '每码时长 (毫秒)'),
    ('size', '二维码边长 (像素)'),
    ('correction', '纠错等级'),
    ('border', '静区 (模块)'),
    ('extra', '完整发送冗余比例'),
)


class SenderApp:
    def __init__(self, root, task=None):
        self.root = root
        self.task = task
        self.pending_source = None
        self.repair_mode = False
        self.requested = None
        self.worker = None
        self.stop_event = threading.Event()
        self.frames = queue.Queue(maxsize=2)
        self.events = queue.Queue()
        self.player = None
        self.closing = False
        self.request_tone = 'muted'
        defaults = load_defaults('sender')
        self.parameters = {
            name: tk.StringVar(root, value=defaults.get(name, value))
            for name, value in DEFAULTS.items()
        }
        self.code = tk.StringVar(root)
        self.heading = tk.StringVar(root)
        self.filename = tk.StringVar(root)
        self.task_path = tk.StringVar(root)
        self.task_id = tk.StringVar(root)
        self.frozen_summary = tk.StringVar(root)
        self.request_status = tk.StringVar(root, value='粘贴或导入补传码后校验')
        self.block_preview = tk.StringVar(root, value='—')
        self.payload_summary = tk.StringVar(root, value='—')
        self.playback_summary = tk.StringVar(root)
        self.status = tk.StringVar(root)

        root.title('二维码发送端')
        root.geometry('620x350')
        root.minsize(530, 320)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.theme = Theme(root)
        toolbar = self.theme.toolbar(root, tasks=[
            ('选择文件', self.new),
            ('打开原发送任务', self.open),
            ('另存发送任务', self.save),
        ], settings=self.settings)
        toolbar.pack(fill='x')
        body = ttk.Frame(root, padding=(18, 12))
        body.pack(fill='both', expand=True)
        ttk.Label(body, textvariable=self.status, style='Muted.TLabel', width=1).pack(
            side='bottom', fill='x', pady=(10, 0),
        )
        self.viewport = tk.Canvas(body, background=self.theme.colors['bg'], highlightthickness=0)
        self.viewport.pack(side='left', fill='both', expand=True)
        self.workflow_scroll = ttk.Scrollbar(body, orient='vertical', command=self.viewport.yview)
        self.viewport.configure(yscrollcommand=self.workflow_scroll.set)
        self.workflow = ttk.Frame(self.viewport)
        self.workflow_item = self.viewport.create_window(0, 0, anchor='nw', window=self.workflow)
        self.viewport.bind('<Configure>', self._layout_workflow)
        self.workflow.bind('<Configure>', self._layout_workflow)
        root.bind('<MouseWheel>', self._scroll_workflow, add=True)
        root.bind('<Button-4>', self._scroll_workflow, add=True)
        root.bind('<Button-5>', self._scroll_workflow, add=True)
        root.bind('<FocusIn>', self._reveal_focus, add=True)
        ttk.Label(self.workflow, textvariable=self.heading, style='Title.TLabel').pack(
            anchor='w', pady=(0, 8),
        )
        self.welcome = ttk.Frame(self.workflow)
        ttk.Button(self.welcome, text='选择文件', command=self.new, style='Primary.TButton').pack(
            fill='x', pady=(0, 8),
        )
        ttk.Button(self.welcome, text='打开原发送任务', command=self.open).pack(fill='x')
        ttk.Label(self.welcome, text='发送多个文件时, 请先打包为 ZIP.', style='Muted.TLabel').pack(
            anchor='w', pady=(10, 0),
        )

        self.creation = ttk.Frame(self.workflow)
        self.creation_path = tk.StringVar(root)
        ttk.Entry(self.creation, textvariable=self.creation_path, state='readonly').pack(fill='x')
        create_fields = ttk.Frame(self.creation)
        create_fields.pack(fill='x', pady=10)
        self.creation_controls = self._create_fields(create_fields, self.parameters)
        self.freeze_button = ttk.Button(self.creation, text='冻结并保存任务', command=self.freeze,
                                        style='Primary.TButton')
        self.freeze_button.pack(fill='x', pady=(0, 8))
        self.cancel_new_button = ttk.Button(self.creation, text='取消', command=self.cancel_new)
        self.cancel_new_button.pack(fill='x')

        self.task_view = ttk.Frame(self.workflow)
        overview = ttk.Frame(self.task_view)
        overview.pack(fill='x')
        overview.columnconfigure(1, weight=1)
        for row, (label, value) in enumerate((
            ('文件', self.filename), ('原发送任务', self.task_path), ('任务标识', self.task_id),
        )):
            ttk.Label(overview, text=label, style='Muted.TLabel').grid(
                row=row, column=0, sticky='w', padx=(0, 10), pady=2,
            )
            ttk.Entry(overview, textvariable=value, state='readonly').grid(
                row=row, column=1, columnspan=2, sticky='ew', pady=2,
            )
        ttk.Label(overview, textvariable=self.frozen_summary, width=1).grid(
            row=3, column=0, columnspan=2, sticky='ew', pady=(4, 0),
        )
        ttk.Button(overview, text='任务详情', command=self.task_details).grid(
            row=3, column=2, sticky='e', padx=(8, 0), pady=(4, 0),
        )

        self.repair_view = ttk.Frame(self.task_view)
        self._build_repair()
        self.playback_view = ttk.Frame(self.task_view)
        self.playback_view.pack(fill='x')
        ttk.Separator(self.playback_view).pack(fill='x', pady=10)
        playback_header = ttk.Frame(self.playback_view)
        playback_header.pack(fill='x')
        self.playback_toggle = ttk.Button(playback_header, text='▸ 播放参数', command=self.toggle_playback)
        self.playback_toggle.pack(side='left', padx=(0, 10))
        ttk.Label(playback_header, textvariable=self.playback_summary, style='Muted.TLabel', width=1).pack(
            side='left', fill='x', expand=True,
        )
        self.playback_fields = ttk.Frame(self.playback_view)
        self.playback_controls = self._playback_fields(self.playback_fields, self.parameters, columns=2)
        self.actions = ttk.Frame(self.task_view)
        self.actions.pack(fill='x')
        ttk.Separator(self.actions).pack(fill='x', pady=10)
        self.start_button = ttk.Button(self.actions, command=self.start, style='Primary.TButton')
        self.start_button.pack(fill='x', pady=(0, 8))
        self.mode_button = ttk.Button(self.actions, command=self.toggle_mode)
        self.mode_button.pack(fill='x')
        self.stop_button = ttk.Button(self.actions, text='停止', command=self.stop)

        self.code.trace_add('write', self._request_edited)
        for name, _ in PLAYBACK_FIELDS:
            self.parameters[name].trace_add('write', self._summarize_playback)
        self.theme.on_change(self._recolor)
        self._summarize_playback()
        self._refresh()
        self.poll_id = self.root.after(25, self.poll)
        self.root.bind('<Destroy>', self._cancel_poll, add=True)

    def _build_repair(self):
        panel = self.repair_view
        ttk.Separator(panel).pack(fill='x', pady=10)
        ttk.Label(panel, text='补传码').pack(anchor='w', pady=(0, 5))
        row = ttk.Frame(panel)
        row.pack(fill='x')
        row.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(row, textvariable=self.code)
        self.entry.grid(row=0, column=0, sticky='ew')
        scroll = ttk.Scrollbar(row, orient='horizontal', command=self.entry.xview)
        scroll.grid(row=1, column=0, sticky='ew')
        self.entry.configure(xscrollcommand=scroll.set)
        self.import_button = ttk.Button(row, text='导入 .qrreq', command=self.import_request)
        self.import_button.grid(row=0, column=1, padx=(8, 0))
        self.apply_button = ttk.Button(panel, text='校验并使用补传码', command=self.apply_request)
        self.apply_button.pack(fill='x', pady=(6, 4))
        self.request_label = ttk.Label(panel, textvariable=self.request_status, width=1)
        self.request_label.pack(fill='x', pady=(4, 6))
        details = ttk.Frame(panel)
        details.pack(fill='x')
        details.columnconfigure(1, weight=1)
        ttk.Label(details, text='补发块号', style='Muted.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 10))
        ttk.Label(details, textvariable=self.block_preview, width=1).grid(row=0, column=1, sticky='ew')
        self.block_button = ttk.Button(details, text='查看全部', command=self.request_details)
        self.block_button.grid(row=0, column=2, sticky='e')
        ttk.Label(details, text='本次补发', style='Muted.TLabel').grid(row=1, column=0, sticky='w', padx=(0, 10))
        ttk.Label(details, textvariable=self.payload_summary, width=1).grid(row=1, column=1, sticky='ew')

    @staticmethod
    def _create_fields(parent, values):
        ttk.Label(parent, text='分块大小 (字节)').pack(side='left', padx=(0, 8))
        entry = ttk.Entry(parent, textvariable=values['chunk_size'], width=8)
        entry.pack(side='left', padx=(0, 16))
        compress = ttk.Checkbutton(parent, text='自动压缩', variable=values['compress'])
        compress.pack(side='left')
        return entry, compress

    @staticmethod
    def _playback_fields(parent, values, columns=1):
        controls = []
        for position, (name, label) in enumerate(PLAYBACK_FIELDS):
            row, column = divmod(position, columns)
            ttk.Label(parent, text=label).grid(row=row, column=2 * column, sticky='w', padx=(0, 8), pady=3)
            if name == 'correction':
                control = ttk.Combobox(parent, values=tuple(ERROR_CORRECTION_LEVELS),
                                       textvariable=values[name], width=7, state='readonly')
            else:
                control = ttk.Entry(parent, textvariable=values[name], width=10)
            control.grid(row=row, column=2 * column + 1, sticky='w', padx=(0, 14), pady=3)
            controls.append(control)
        return controls

    @staticmethod
    def _creation_values(values):
        chunk_size = int(values['chunk_size'].get())
        compress = values['compress'].get()
        if not 1 <= chunk_size <= MAX_CHUNK_SIZE or compress not in ('0', '1'):
            raise ValueError(f'分块大小须为 1–{MAX_CHUNK_SIZE} 字节, 自动压缩须为启用或关闭.')
        return chunk_size, compress == '1'

    def _playback_values(self, values):
        interval, size, border = (int(values[name].get()) for name in ('interval', 'size', 'border'))
        extra, correction = float(values['extra'].get()), values['correction'].get()
        max_size = min(self.root.winfo_screenheight() - 160, self.root.winfo_screenwidth() - 80)
        if not 1 <= interval <= 2_147_483_647 or not 200 <= size <= max_size:
            raise ValueError(f'时长须为正整数且不超过 2,147,483,647 毫秒, 边长须为 200–{max_size} 像素.')
        if not 4 <= border <= size // 2 or extra < 0 or not math.isfinite(extra):
            raise ValueError('静区须为 4 模块到边长一半之间的整数, 冗余比例须为非负有限数.')
        if correction not in ERROR_CORRECTION_LEVELS:
            raise ValueError('请选择 L、M、Q 或 H 纠错等级.')
        return interval, size, border, extra, ERROR_CORRECTION_LEVELS[correction]

    def _summarize_playback(self, *_):
        values = {name: self.parameters[name].get() for name, _ in PLAYBACK_FIELDS}
        self.playback_summary.set(f"{values['interval']} 毫秒 / {values['size']} 像素 / 纠错 {values['correction']}")

    def _recolor(self):
        colors = self.theme.colors
        self.viewport.configure(background=colors['bg'])
        self.request_label.configure(foreground=colors[self.request_tone])
        if self.player is not None:
            self.player.configure(bg=colors['bg'])
            self.bar.configure(background=colors['field'])
            self._draw_progress()

    @property
    def busy(self):
        return self.worker is not None or self.player is not None

    def _refresh(self):
        if self.pending_source is not None:
            view, title = self.creation, '建立发送任务'
        elif self.task is None:
            view, title = self.welcome, '发送文件'
        else:
            view, title = self.task_view, '定向补传' if self.repair_mode else '完整发送'
        for panel in (self.welcome, self.creation, self.task_view):
            if panel is not view:
                panel.pack_forget()
        view.pack(fill='x')
        self.heading.set(title)
        if self.task is not None:
            params = self.task.parameters
            self.filename.set(self.task.manifest['filename'])
            self.task_path.set(self.task.state_path.name if self.task.state_path is not None else '尚未保存')
            self.task_id.set(params.task_id)
            self.frozen_summary.set(f'{params.num_chunks:,} 块 × {params.chunk_size:,} 字节  ·  {self._compression()}')
        if self.repair_mode:
            self.repair_view.pack(fill='x', before=self.playback_view)
        else:
            self.repair_view.pack_forget()
        self.start_button.configure(text='开始补传' if self.repair_mode else '开始发送')
        self.mode_button.configure(text='切回完整发送' if self.repair_mode else '定向补传')
        self._refresh_controls()
        self._fit_workflow()

    def _refresh_controls(self):
        blocked = self.busy
        enabled = self.task is not None and not blocked and (not self.repair_mode or bool(self.requested))
        self.start_button.state(['!disabled'] if enabled else ['disabled'])
        for control in (self.freeze_button, self.cancel_new_button, self.mode_button, self.entry,
                        self.import_button, self.apply_button, *self.creation_controls, *self.playback_controls):
            control.state(['disabled'] if blocked else ['!disabled'])
        self.block_button.state(['!disabled'] if self.requested else ['disabled'])
        if self.player is not None:
            self.stop_button.pack(fill='x', pady=(8, 0))
        else:
            self.stop_button.pack_forget()

    def _fit_workflow(self):
        self.root.update_idletasks()
        self.viewport.configure(height=self.workflow.winfo_reqheight())
        self.root.update_idletasks()
        # 为系统标题栏和任务栏预留空间, 较高的内容在窗口内滚动.
        usable = self.root.winfo_screenheight() - 100
        self.root.minsize(530, min(usable, max(320, self.root.winfo_reqheight())))
        self._layout_workflow()

    def _layout_workflow(self, _=None):
        width, height = self.viewport.winfo_width(), self.workflow.winfo_reqheight()
        self.viewport.itemconfigure(self.workflow_item, width=width)
        self.viewport.configure(scrollregion=(0, 0, width, height))
        if height > self.viewport.winfo_height():
            self.workflow_scroll.pack(side='right', fill='y', before=self.viewport)
        else:
            self.workflow_scroll.pack_forget()
            self.viewport.yview_moveto(0)

    def _scroll_workflow(self, event):
        if self.workflow_scroll.winfo_ismapped():
            up = event.num == 4 or event.delta > 0
            self.viewport.yview_scroll(-1 if up else 1, 'units')

    def _reveal_focus(self, event):
        widget = event.widget
        if not self.workflow_scroll.winfo_ismapped() or not str(widget).startswith(str(self.workflow) + '.'):
            return
        top = widget.winfo_rooty() - self.viewport.winfo_rooty()
        below = top + widget.winfo_height() - self.viewport.winfo_height()
        if top < 0 or below > 0:
            offset = top if top < 0 else below
            self.viewport.yview_moveto(self.viewport.yview()[0] + offset / self.workflow.winfo_reqheight())

    def new(self):
        if self.busy:
            return
        name = filedialog.askopenfilename(parent=self.root, title='选择发送文件')
        if name:
            self.pending_source = Path(name)
            self.creation_path.set(name)
            self.status.set('')
            self._refresh()
            self.viewport.yview_moveto(0)

    def cancel_new(self):
        self.pending_source = None
        self._refresh()
        self.viewport.yview_moveto(0)

    def freeze(self):
        if self.busy or self.pending_source is None:
            return
        try:
            chunk_size, compress = self._creation_values(self.parameters)
        except ValueError as exc:
            messagebox.showerror('分块参数有误', str(exc), parent=self.root)
            return
        source = self.pending_source
        name = filedialog.asksaveasfilename(parent=self.root, title='保存冻结的发送任务',
                                            defaultextension='.qrtx', initialfile=source.name + '.qrtx')
        if not name:
            return
        target = Path(name)
        if target.resolve() == source.resolve():
            messagebox.showerror('无法保存任务', '发送任务与待发送文件必须使用不同路径.', parent=self.root)
            return

        def prepare():
            try:
                task = SendTask.create(source, chunk_size, compress)
                task.save(target)
                self.events.put(('task', task))
            except (OSError, ValueError) as exc:
                self.events.put(('prepare_error', str(exc)))

        self.status.set('正在读取文件并保存冻结任务…')
        self.worker = threading.Thread(target=prepare)
        self.worker.start()
        self._refresh_controls()

    def _set_task(self, task, repair_mode):
        self.task = task
        self.repair_mode = repair_mode
        self.pending_source = None
        self.code.set('')
        self._refresh()
        self.viewport.yview_moveto(0)

    def open(self):
        if self.busy:
            return
        name = filedialog.askopenfilename(parent=self.root, title='打开原发送任务', filetypes=[('发送任务', '*.qrtx')])
        if name:
            try:
                task = SendTask.load(Path(name))
            except (OSError, ValueError) as exc:
                messagebox.showerror('无法打开任务', str(exc), parent=self.root)
                return
            self._set_task(task, repair_mode=True)
            self.status.set('已打开冻结任务')

    def save(self):
        if self.busy or self.task is None:
            return
        name = filedialog.asksaveasfilename(parent=self.root, title='另存发送任务', defaultextension='.qrtx')
        if name:
            try:
                self.task.save(Path(name))
            except OSError as exc:
                messagebox.showerror('保存失败', str(exc), parent=self.root)
                return
            self._refresh()
            self.status.set('发送任务已保存')

    def _request_edited(self, *_):
        code = self.code.get()
        entered_repair = bool(code) and not self.repair_mode
        if entered_repair:
            self.repair_mode = True
        self._set_request(None, '请求待校验' if code else '粘贴或导入补传码后校验', 'muted')
        if entered_repair:
            self._refresh()
        else:
            self._refresh_controls()

    def _set_request(self, requested, status, tone):
        self.requested = requested
        self.request_status.set(status)
        self.request_tone = tone
        self.request_label.configure(foreground=self.theme.colors[tone])
        if requested:
            preview = '、'.join(f'#{index}' for index in requested[:5])
            self.block_preview.set(preview + ('…' if len(requested) > 5 else ''))
        else:
            self.block_preview.set('—')
        if requested is None:
            self.payload_summary.set('—')
        else:
            count = len(requested)
            self.payload_summary.set(f'{count:,} 块 / {count * self.task.parameters.chunk_size:,} 字节块载荷')

    def import_request(self):
        if self.busy or self.task is None:
            return
        name = filedialog.askopenfilename(parent=self.root, title='导入补传请求',
                                          filetypes=[('补传请求', '*.qrreq'), ('所有文件', '*')])
        if name:
            self.repair_mode = True
            self.code.set('')
            try:
                self.code.set(Path(name).read_text(encoding='utf-8').strip())
            except (OSError, UnicodeError) as exc:
                self._set_request(None, '请求文件读取失败', 'red')
                messagebox.showerror('请求文件读取失败', str(exc), parent=self.root)
                return
            self.apply_request()

    def apply_request(self):
        if self.busy or self.task is None:
            return
        self.repair_mode = True
        try:
            feedback = self.task.request(self.code.get())
        except ValueError as exc:
            self._set_request(None, '校验失败', 'red')
            self._refresh()
            messagebox.showerror('补传请求无效', str(exc), parent=self.root)
            return
        self.use_request(feedback)

    def use_request(self, feedback):
        """使用已由当前冻结任务校验的补传请求."""
        self.repair_mode = True
        if self.code.get() != feedback.code:
            self.code.set(feedback.code)
        status = '校验通过 · 与当前任务匹配' if feedback.requested else '校验通过 · 没有待补发的源块'
        self._set_request(feedback.requested, status, 'green')
        self._refresh()

    def toggle_mode(self):
        if self.busy:
            return
        self.repair_mode = not self.repair_mode
        self.code.set('')
        self.status.set('')
        self._refresh()
        if self.repair_mode:
            self.entry.focus_set()

    def toggle_playback(self):
        if self.playback_fields.winfo_manager():
            self.playback_fields.pack_forget()
            self.playback_toggle.configure(text='▸ 播放参数')
        else:
            self.playback_fields.pack(fill='x', pady=(6, 0))
            self.playback_toggle.configure(text='▾ 播放参数')
        self._fit_workflow()

    def _dialog(self, title):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.configure(bg=self.theme.colors['bg'])
        dialog.bind('<Escape>', lambda _: dialog.destroy())
        return dialog

    def _show_details(self, title, content):
        dialog = self._dialog(title)
        dialog.geometry('560x320')
        dialog.minsize(350, 190)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        colors = self.theme.colors
        view = tk.Text(dialog, wrap='word', bg=colors['field'], fg=colors['fg'],
                       font='TkDefaultFont', padx=10, pady=8,
                       selectbackground=colors['accent'], selectforeground=colors['accent_fg'],
                       highlightbackground=colors['border'], highlightcolor=colors['accent'],
                       relief='flat', borderwidth=0)
        view.grid(row=0, column=0, sticky='nsew', padx=(12, 0), pady=(12, 0))
        scroll = ttk.Scrollbar(dialog, orient='vertical', command=view.yview)
        scroll.grid(row=0, column=1, sticky='ns', padx=(0, 12), pady=(12, 0))
        view.configure(yscrollcommand=scroll.set)
        view.insert('1.0', content)
        view.configure(state='disabled')
        ttk.Button(dialog, text='关闭', command=dialog.destroy).grid(row=1, column=0, columnspan=2, pady=8)
        dialog.grab_set()
        view.focus_set()

    def _compression(self):
        return 'zlib' if self.task.manifest['compression'] == 'zlib' else '未压缩'

    def task_details(self):
        task = self.task
        params = task.parameters
        manifest = task.manifest
        path = task.state_path if task.state_path is not None else '尚未保存'
        self._show_details(
            '冻结任务详情',
            f"文件  {manifest['filename']}\n原始大小  {manifest['size']:,} 字节\n"
            f"原文件路径  {task.source_path}\n任务文件  {path}\n"
            f"任务标识  {params.task_id}\nSHA-256  {manifest['sha256']}\n"
            f"分块  {params.num_chunks:,} 块 × {params.chunk_size:,} 字节\n"
            f"实际压缩  {self._compression()}\n"
            f"编码数据  {params.payload_size:,} 字节\n末块填充  {params.padding:,} 字节",
        )

    def request_details(self):
        blocks = '\n'.join(
            f'#{start}' if count == 1 else f'#{start}–#{start + count - 1}'
            for start, count in ranges(self.requested)
        )
        self._show_details('待补发源块', f'{self.payload_summary.get()}\n\n{blocks}')

    def settings(self):
        if self.busy:
            return
        dialog = self._dialog('发送参数设置')
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill='both', expand=True)
        values = {key: tk.StringVar(dialog, value=variable.get()) for key, variable in self.parameters.items()}
        ttk.Label(body, text='新任务分块', style='Title.TLabel').pack(anchor='w')
        create = ttk.Frame(body)
        create.pack(fill='x', pady=(8, 12))
        self._create_fields(create, values)
        ttk.Label(body, text='播放参数', style='Title.TLabel').pack(anchor='w')
        playback = ttk.Frame(body)
        playback.pack(fill='x', pady=(6, 12))
        self._playback_fields(playback, values)
        status = tk.StringVar(dialog, value='默认值用于下次打开发送窗口.')
        ttk.Label(body, textvariable=status, style='Muted.TLabel').pack(anchor='w', pady=(0, 10))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x')

        def submit(persist):
            try:
                self._creation_values(values)
                self._playback_values(values)
                if persist:
                    save_defaults('sender', {name: variable.get() for name, variable in values.items()})
            except (OSError, ValueError) as exc:
                messagebox.showerror('参数设置失败', str(exc), parent=dialog)
                return
            if persist:
                status.set('默认值已保存')
            else:
                for name, variable in values.items():
                    self.parameters[name].set(variable.get())
                dialog.destroy()

        ttk.Button(buttons, text='应用本次', style='Primary.TButton', command=lambda: submit(False)).pack(
            side='left', padx=(0, 8),
        )
        ttk.Button(buttons, text='保存为默认值', command=lambda: submit(True)).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='关闭', command=dialog.destroy).pack(side='right')
        dialog.grab_set()
        dialog.focus_set()

    @staticmethod
    def _produce(task, requested, size, frames, events, stop, extra=0.5, border=4,
                 correction=ERROR_CORRECTION_LEVELS['M']):
        try:
            for packet, offset, total in task.rounds(requested, extra):
                if stop.is_set():
                    return
                entry = (build_qr(packet.text(), size, border, correction), packet, offset, total)
                while not stop.is_set():
                    try:
                        frames.put(entry, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
        except (OSError, ValueError) as exc:
            events.put(('play_error', str(exc)))

    def start(self):
        if self.busy or self.task is None or self.pending_source is not None:
            return
        if self.repair_mode and not self.requested:
            return
        try:
            interval, size, border, extra, correction = self._playback_values(self.parameters)
            if not self.repair_mode and not math.isfinite(extra * self.task.parameters.num_chunks):
                raise ValueError('完整发送冗余比例与当前任务的块数乘积过大.')
        except ValueError as exc:
            messagebox.showerror('播放参数有误', str(exc), parent=self.root)
            return
        self.interval_ms = interval
        self.next_frame = 0
        self.progress = None
        self.frames = queue.Queue(maxsize=2)
        self.stop_event.clear()
        self.player = tk.Toplevel(self.root)
        mode = '定向补传' if self.repair_mode else '完整发送'
        self.player.title(f"{mode} | {self.task.manifest['filename']}")
        self.player.resizable(False, False)
        self.player.protocol('WM_DELETE_WINDOW', self.stop)
        self.player.bind('<Escape>', lambda _: self.stop())
        player_body = ttk.Frame(self.player, padding=10)
        player_body.pack()
        self.image_area = tk.Canvas(player_body, width=size, height=size, background='white', highlightthickness=0)
        self.image_area.pack()
        self.image_item = self.image_area.create_image(0, 0, anchor='nw')
        self.round_label = ttk.Label(player_body, text='正在生成二维码…', width=42, anchor='center')
        self.round_label.pack(fill='x', pady=(6, 2))
        self.packet_label = ttk.Label(player_body, width=1, anchor='center')
        self.packet_label.pack(fill='x')
        self.bar = tk.Canvas(player_body, width=size, height=8, highlightthickness=0)
        self.bar.pack(pady=(4, 6))
        ttk.Button(player_body, text='停止', command=self.stop).pack(fill='x')
        self._recolor()
        self.worker = threading.Thread(
            target=self._produce,
            args=(self.task, self.requested if self.repair_mode else None, size, self.frames,
                  self.events, self.stop_event, extra, border, correction),
        )
        self.worker.start()
        self.status.set('正在补传请求中的源块' if self.repair_mode else '正在发送完整任务')
        self._refresh_controls()
        self._fit_workflow()

    def stop(self):
        self.stop_event.set()
        if self.player is not None:
            self.player.destroy()
            self.player = None
            self.photo = None
        self.status.set('正在停止…' if self.worker is not None else '已停止')
        self._refresh_controls()
        self._fit_workflow()

    def close(self):
        self.closing = True
        self.stop()

    def _draw_progress(self):
        if self.progress is None:
            return
        round_no, fraction, width = self.progress
        colors = self.theme.colors
        color = colors[('accent', 'green', 'yellow')[(round_no - 1) % 3]]
        self.bar.delete('all')
        self.bar.create_rectangle(0, 0, width * fraction, 8, fill=color, outline='')

    def _display_frame(self):
        try:
            frame, packet, offset, total = self.frames.get_nowait()
        except queue.Empty:
            return
        self.photo = ImageTk.PhotoImage(frame, master=self.player)
        self.image_area.itemconfigure(self.image_item, image=self.photo)
        if offset == total == 1:
            boundary = '本轮开始 / 结束'
        elif offset == 1:
            boundary = '本轮开始'
        elif offset == total:
            boundary = '本轮结束'
        else:
            boundary = '传输中'
        self.round_label.configure(text=f'第 {packet.round_no} 轮  ·  第 {offset}/{total} 码  ·  {boundary}')
        packet_label = f'原始源块 #{packet.number}' if packet.kind == SOURCE else f'喷泉修复包  ·  种子 {packet.number}'
        self.packet_label.configure(text=packet_label)
        self.progress = packet.round_no, offset / total, frame.width
        self._draw_progress()
        self.next_frame = time.monotonic() + self.interval_ms / 1000

    def poll(self):
        self.poll_id = None
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if self.closing:
                continue
            if kind == 'task':
                self._set_task(value, repair_mode=False)
                self.status.set('冻结任务已保存')
            else:
                if kind == 'play_error':
                    self.stop()
                    title = '发送失败'
                else:
                    title = '冻结任务失败'
                self.status.set(title)
                messagebox.showerror(title, value, parent=self.root)
        if self.worker is not None and not self.worker.is_alive():
            self.worker.join()
            self.worker = None
            self._refresh_controls()
            if self.status.get() == '正在停止…':
                self.status.set('已停止')
        if self.closing and self.worker is None:
            self.root.destroy()
            return
        if self.player is not None and time.monotonic() >= self.next_frame:
            self._display_frame()
        self.poll_id = self.root.after(25, self.poll)

    def _cancel_poll(self, event):
        if event.widget is self.root and self.poll_id is not None:
            self.root.after_cancel(self.poll_id)


def main():
    root = tk.Tk()
    if '--self-test' in sys.argv:
        root.withdraw()
        SenderApp(root)
        image = ImageTk.PhotoImage(Image.new('RGB', (2, 2)))
        root.update()
        root.destroy()
        return
    SenderApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
