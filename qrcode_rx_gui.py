"""接收窗口: 视频导入、屏幕录制、现场续收和源块补传."""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from mss.exception import ScreenShotError

from block_map import cells
from block_map_view import BlockMapView
from legacy_receive import receive_legacy_video
from receive_pipeline import process_video
from screen_record import record_screen
from transfer_state import ReceiveTask, atomic_write
from ui_theme import Theme, load_defaults, save_defaults


class ReceiverApp:
    modes = {'自动最短': 'auto', '缺块模式': 'missing', '成功模式': 'success', '位图模式': 'bitmap', '全部重发': 'all'}

    def __init__(self, root):
        self.root, self.task = root, None
        self.worker = None
        self.events = queue.Queue()
        self.stop_event, self.close_event = threading.Event(), threading.Event()
        self.selected = set()
        self.map_cells, self.missing = [], []
        self.saved = True
        self.log_lines, self.text_widgets = [], set()
        self.log_widget = None
        self.phase = 'idle'
        self.wait_after = None
        self.poll_after = None
        self.layout_after = None

        root.title('二维码接收端')
        root.geometry(f'780x{min(800, root.winfo_screenheight() - 90)}')
        root.minsize(565, 630)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.theme = Theme(root)
        self.theme.toolbar(root, tasks=[
            ('新建接收任务', self.new),
            ('继续接收现场…', self.open),
            ('保存现场', self.save),
            ('另存现场…', lambda: self.save(True)),
            ('旧视频一次性解析…', lambda: self.import_video(True)),
            ('查看接收日志', self.show_log),
        ], settings=self.settings).pack(fill='x')
        ttk.Separator(root).pack(fill='x')

        defaults = load_defaults('receiver')
        self.output = tk.StringVar(value=defaults.get('output', str(Path('received').resolve())))
        self.fps = tk.StringVar(value=defaults.get('fps', '30'))
        self.monitor = tk.StringVar(value=defaults.get('monitor', '1'))
        self.heading, self.status, self.counts, self.scan = (tk.StringVar() for _ in range(4))

        body = ttk.Frame(root, padding=(20, 12, 20, 12))
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky='ew')
        self.title_label = ttk.Label(header, textvariable=self.heading, style='Title.TLabel')
        self.title_label.grid(row=0, column=0, sticky='w')
        self.wait_indicator = ttk.Label(header, style='Title.TLabel', width=4)
        self.wait_indicator.grid(row=0, column=1, sticky='w', padx=(2, 0))
        self.counts_label = ttk.Label(body, textvariable=self.counts)
        self.counts_label.grid(row=1, column=0, sticky='ew', pady=(2, 3))
        self.scan_label = ttk.Label(body, textvariable=self.scan, style='Muted.TLabel')
        self.scan_label.grid(row=2, column=0, sticky='ew')
        self.progress = ttk.Progressbar(body)
        self.progress.grid(row=3, column=0, sticky='ew', pady=(4, 8))
        self.status_separator = ttk.Separator(body)
        self.status_separator.grid(row=4, column=0, sticky='ew')

        self.idle_directory = ttk.Frame(body)
        self.idle_directory.grid(row=2, column=0, sticky='ew', pady=(9, 0))
        self.idle_directory.columnconfigure(1, weight=1)
        ttk.Label(self.idle_directory, text='接收目录').grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(self.idle_directory, textvariable=self.output, state='readonly', width=1).grid(
            row=0, column=1, sticky='ew')
        ttk.Button(self.idle_directory, text='修改…', command=self.settings).grid(row=0, column=2, padx=(8, 0))

        self.content_host = ttk.Frame(body)
        self.content_host.grid(row=5, column=0, sticky='nsew', pady=(8, 3))
        self.content_host.columnconfigure(0, weight=1)
        self.content_host.rowconfigure(0, weight=1)
        self.viewport = tk.Canvas(self.content_host, width=1, height=1, borderwidth=0, highlightthickness=0)
        self.viewport.grid(row=0, column=0, sticky='nsew')
        self.content_scroll = ttk.Scrollbar(self.content_host, orient='vertical', command=self.viewport.yview)
        self.content_scroll.grid(row=0, column=1, sticky='ns')
        self.content_scroll.grid_remove()
        self.viewport.configure(yscrollcommand=self.content_scroll.set)
        self.content = ttk.Frame(self.viewport)
        self.content_window = self.viewport.create_window(0, 0, window=self.content, anchor='nw')
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.map_panel = ttk.Frame(self.content)
        self.map_panel.grid(row=0, column=0, sticky='nsew')
        ttk.Label(self.map_panel, text='源块恢复图', style='ReceiverHeading.TLabel').pack(anchor='w')
        self.map_view = BlockMapView(self.map_panel, self.theme, self.selected, self.select_cell, self.cell_details)
        self.map_view.pack(fill='both', expand=True)

        self.prompt = ttk.Frame(self.content)
        self.prompt.grid(row=0, column=0, sticky='nsew')
        self.prompt.columnconfigure(0, weight=1)
        self.prompt.rowconfigure((0, 2), weight=1)
        self.prompt_text = tk.StringVar()
        ttk.Label(self.prompt, textvariable=self.prompt_text, style='Muted.TLabel', anchor='center',
                  justify='center', wraplength=470).grid(row=1, column=0, sticky='ew')
        self.build_repair()
        self.build_result()
        self.content_host.bind('<Configure>', self.schedule_content_layout)
        self.content_host.bind('<Map>', self.schedule_content_layout)
        root.bind('<FocusIn>', self.reveal_content, add='+')

        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, sticky='ew', pady=(7, 0))
        self.actions = actions
        actions.columnconfigure((0, 1, 2), weight=1, uniform='input')
        self.import_button = ttk.Button(actions, command=self.import_video, style='Primary.TButton')
        self.import_button.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.record_button = ttk.Button(actions, text='录制屏幕…', command=self.record)
        self.record_button.grid(row=0, column=1, sticky='ew', padx=4)
        self.open_button = ttk.Button(actions, text='继续现场…', command=self.open)
        self.open_button.grid(row=0, column=2, sticky='ew', padx=(4, 0))
        self.stop_button = ttk.Button(actions, text='停止解析', command=self.stop, style='Primary.TButton')
        self.stop_button.grid(row=0, column=0, columnspan=3, sticky='ew')
        footer = ttk.Frame(body)
        footer.grid(row=7, column=0, sticky='ew', pady=(4, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', width=1).grid(row=0, column=0, sticky='ew')
        ttk.Button(footer, text='接收日志', command=self.show_log).grid(row=0, column=1, padx=(8, 0))

        self.theme.on_change(self.apply_colors)
        self.apply_colors()
        self.show_idle()
        self.poll_after = root.after(60, self.poll)
        root.bind('<Destroy>', self.cancel_timers, add='+')

    def build_repair(self):
        self.repair = ttk.Frame(self.content)
        self.repair.grid(row=1, column=0, sticky='ew')
        self.repair.columnconfigure((1, 3), weight=1, uniform='scope')
        ttk.Separator(self.repair).grid(row=0, column=0, columnspan=5, sticky='ew', pady=(4, 2))
        ttk.Label(self.repair, text='待补源块').grid(row=1, column=0, sticky='w', padx=(0, 12))
        self.missing_summary = tk.StringVar()
        ttk.Label(self.repair, textvariable=self.missing_summary, width=1).grid(
            row=1, column=1, columnspan=3, sticky='ew')
        ttk.Button(self.repair, text='查看全部', command=self.show_missing).grid(row=1, column=4, sticky='e')
        ttk.Separator(self.repair).grid(row=2, column=0, columnspan=5, sticky='ew', pady=(4, 2))
        ttk.Label(self.repair, text='补传请求', style='ReceiverHeading.TLabel').grid(row=3, column=0, columnspan=5, sticky='w')

        self.scope, self.mode = tk.StringVar(value='整个任务'), tk.StringVar(value='自动最短')
        ttk.Label(self.repair, text='范围').grid(row=4, column=0, sticky='w')
        ttk.Combobox(self.repair, textvariable=self.scope, values=('整个任务', '选中区域'),
                     state='readonly', width=8).grid(row=4, column=1, sticky='ew')
        ttk.Label(self.repair, text='编码').grid(row=4, column=2, sticky='w', padx=(12, 6))
        ttk.Combobox(self.repair, textvariable=self.mode, values=tuple(self.modes), state='readonly', width=8).grid(
            row=4, column=3, sticky='ew')
        ttk.Button(self.repair, text='清除选区', command=self.clear_selection).grid(row=4, column=4, padx=(8, 0))
        ttk.Button(self.repair, text='生成补传码', command=self.generate, style='Primary.TButton').grid(
            row=5, column=0, columnspan=5, sticky='ew', pady=(4, 2))

        code_area = ttk.Frame(self.repair)
        code_area.grid(row=6, column=0, columnspan=5, sticky='ew')
        code_area.columnconfigure((0, 1), weight=1, uniform='request')
        self.code_summary = tk.StringVar()
        ttk.Label(code_area, textvariable=self.code_summary, style='Muted.TLabel', width=1).grid(
            row=0, column=0, columnspan=2, sticky='ew')
        preview = ttk.Frame(code_area)
        preview.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        preview.columnconfigure(0, weight=1)
        self.view_code_button = ttk.Button(preview, text='查看完整补传码',
                                           command=lambda: self.show_text('补传码', self.feedback_code, 'char'))
        self.view_code_button.grid(row=0, column=1, sticky='e', padx=(8, 0))
        self.code = tk.StringVar()
        ttk.Entry(preview, textvariable=self.code, state='readonly', width=1).grid(row=0, column=0, sticky='ew')
        self.copy_button = ttk.Button(code_area, text='复制补传码', command=self.copy)
        self.copy_button.grid(row=2, column=0, sticky='ew', padx=(0, 4))
        self.export_button = ttk.Button(code_area, text='导出 .qrreq…', command=self.export)
        self.export_button.grid(row=2, column=1, sticky='ew', padx=(4, 0))
        self.scope.trace_add('write', lambda *_: self.clear_code())
        self.mode.trace_add('write', lambda *_: self.clear_code())
        self.clear_code()

    def build_result(self):
        self.result = ttk.Frame(self.content)
        self.result.grid(row=1, column=0, sticky='ew')
        self.result.columnconfigure(1, weight=1)
        ttk.Separator(self.result).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(6, 4))
        ttk.Label(self.result, text='接收结果', style='ReceiverHeading.TLabel').grid(row=1, column=0, columnspan=2, sticky='w')
        self.result_values = [tk.StringVar() for _ in range(4)]
        labels = ('文件名', '原始大小', 'SHA-256', '保存路径')
        for row, (label, value) in enumerate(zip(labels, self.result_values), start=2):
            ttk.Label(self.result, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12))
            ttk.Entry(self.result, textvariable=value, state='readonly', width=1).grid(
                row=row, column=1, sticky='ew', pady=2)
        actions = ttk.Frame(self.result)
        actions.grid(row=6, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.result_button = ttk.Button(actions, text='打开结果目录', command=self.open_result)
        self.result_button.pack(side='left')
        ttk.Button(actions, text='保存到接收目录', command=self.save_output).pack(side='left', padx=8)
        ttk.Button(actions, text='查看完整结果', command=lambda: self.show_text(
            '接收结果', '\n'.join(f'{label}：{value.get()}' for label, value in zip(labels, self.result_values))
        )).pack(side='left')

    def apply_colors(self):
        palette = self.theme.colors
        self.viewport.configure(background=palette['bg'])
        style = ttk.Style(self.root)
        style.configure('ReceiverHeading.TLabel', font=('TkDefaultFont', 12, 'bold'))
        style.configure('Success.Title.TLabel', foreground=palette['green'])
        style.configure('Error.Title.TLabel', foreground=palette['red'])
        for widget in self.text_widgets:
            widget.configure(bg=palette['field'], fg=palette['fg'], selectbackground=palette['accent'],
                             selectforeground=palette['accent_fg'], highlightbackground=palette['border'],
                             highlightcolor=palette['accent'])

    def show_text(self, title, content, wrap='word'):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.transient(self.root)
        panel = ttk.Frame(window, padding=10)
        panel.pack(fill='both', expand=True)
        field = tk.Text(panel, width=74, height=17, wrap=wrap, borderwidth=0, highlightthickness=1)
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=field.yview)
        field.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        field.pack(side='left', fill='both', expand=True)
        field.insert('1.0', content)
        field.configure(state='disabled')
        self.text_widgets.add(field)
        field.bind('<Destroy>', lambda _: self.text_widgets.remove(field))
        window.bind('<Escape>', lambda _: window.destroy())
        self.apply_colors()
        return field

    def append_log(self, text):
        self.log_lines.append(text)
        if self.log_widget is not None:
            self.log_widget.configure(state='normal')
            self.log_widget.insert('end', text + '\n')
            self.log_widget.see('end')
            self.log_widget.configure(state='disabled')

    def show_log(self):
        if self.log_widget is None:
            self.log_widget = self.show_text('接收日志', ''.join(line + '\n' for line in self.log_lines))
            self.log_widget.bind('<Destroy>', lambda _: setattr(self, 'log_widget', None), add='+')
        self.log_widget.winfo_toplevel().lift()

    def show_missing(self):
        self.show_text(f'待补源块 · {len(self.missing):,} 块', ', '.join(map(str, self.missing)))

    def show_idle(self):
        self.missing = []
        self.counts.set('')
        self.scan.set('')
        self.draw([])
        self.set_state('idle', '准备接收')

    def animate_wait(self, step=1):
        self.wait_indicator.configure(text='·' * step)
        self.wait_after = self.root.after(320, self.animate_wait, step % 3 + 1)

    def cancel_wait(self):
        if self.wait_after is not None:
            self.root.after_cancel(self.wait_after)
            self.wait_after = None

    def cancel_timers(self, event):
        if event.widget is self.root:
            self.cancel_wait()
            if self.poll_after is not None:
                self.root.after_cancel(self.poll_after)
                self.poll_after = None
            if self.layout_after is not None:
                self.root.after_cancel(self.layout_after)
                self.layout_after = None

    def schedule_content_layout(self, _event=None):
        if self.layout_after is None:
            self.layout_after = self.root.after_idle(self.layout_content)

    def layout_content(self):
        """图表预留两行. 大字体或高度不足时只滚动内容区, 保留顶部和底部操作."""
        self.layout_after = None
        if not self.content_host.winfo_ismapped():
            return
        width, height = self.content_host.winfo_width(), self.content_host.winfo_height()
        minimum = self.prompt.winfo_reqheight()
        if self.map_panel.winfo_manager():
            minimum = self.map_panel.winfo_reqheight() - self.map_view.winfo_reqheight() + self.map_view.minimum_height
            for panel in (self.repair, self.result):
                if panel.winfo_manager():
                    minimum += panel.winfo_reqheight()
        if minimum > height:
            self.content_scroll.grid()
            width -= self.content_scroll.winfo_reqwidth()
        else:
            self.content_scroll.grid_remove()
            self.viewport.yview_moveto(0)
        extent = max(minimum, height)
        self.viewport.itemconfigure(self.content_window, width=width, height=extent)
        self.viewport.configure(scrollregion=(0, 0, width, extent))

    def reveal_content(self, event):
        if not self.content_scroll.winfo_ismapped() or not str(event.widget).startswith(str(self.content) + '.'):
            return
        top = event.widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + event.widget.winfo_height()
        start = self.viewport.canvasy(0)
        height = self.viewport.winfo_height()
        extent = self.content.winfo_height()
        if top < start:
            self.viewport.yview_moveto(top / extent)
        elif bottom > start + height:
            self.viewport.yview_moveto((bottom - height) / extent)

    def set_state(self, phase, title, detail='', prompt=''):
        """切换可用区域, 使任务操作在同一阶段保持原位."""
        if self.phase != phase:
            self.viewport.yview_moveto(0)
        self.phase = phase
        self.heading.set(title)
        self.status.set(detail)
        self.prompt_text.set(prompt)
        styles = {'success': 'Success.Title.TLabel', 'error': 'Error.Title.TLabel', 'state_error': 'Error.Title.TLabel'}
        style = styles.get(phase, 'Title.TLabel')
        self.title_label.configure(style=style)
        self.cancel_wait()
        if phase == 'waiting':
            self.wait_indicator.grid()
            self.animate_wait()
        else:
            self.wait_indicator.grid_remove()
        if phase == 'idle':
            for widget in (self.counts_label, self.scan_label, self.progress, self.status_separator, self.content_host):
                widget.grid_remove()
            self.idle_directory.grid()
            self.actions.grid_configure(row=1)
        else:
            self.idle_directory.grid_remove()
            for widget in (self.counts_label, self.scan_label, self.status_separator, self.content_host):
                widget.grid()
            self.actions.grid_configure(row=6)
        busy = self.worker is not None
        if busy:
            self.progress.grid()
        else:
            self.progress.grid_remove()
        task_visible = bool(self.map_cells) and (not busy or phase == 'parsing')
        if task_visible:
            self.prompt.grid_remove()
            self.map_panel.grid()
        else:
            self.map_panel.grid_remove()
            self.prompt.grid()
        if not busy and self.task is not None and self.task.verified:
            self.result.grid()
        else:
            self.result.grid_remove()
        if task_visible and not busy and self.missing:
            self.repair.grid()
        else:
            self.repair.grid_remove()

        if phase in ('recording', 'saving_recording', 'legacy'):
            if str(self.progress.cget('mode')) != 'indeterminate':
                self.progress.configure(mode='indeterminate', value=0)
                self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate', maximum=1, value=0)
        if busy:
            self.import_button.grid_remove()
            self.record_button.grid_remove()
            self.open_button.grid_remove()
            label = '停止录制并解析' if phase == 'recording' else '停止解析'
            self.stop_button.configure(text=label)
            self.stop_button.state(['disabled'] if self.stop_event.is_set() else ['!disabled'])
            self.stop_button.grid()
        else:
            self.stop_button.grid_remove()
            self.import_button.configure(text='追加视频…' if self.task is not None else '导入视频…')
            self.import_button.grid()
            self.record_button.grid()
            self.open_button.grid()
            if self.task is not None:
                self.progress.configure(maximum=self.task.parameters.num_chunks, value=self.task.done)
        self.schedule_content_layout()

    def save(self, choose=False):
        if self.worker is not None:
            return False
        if self.task is None:
            self.status.set('当前没有需要保存的接收现场。')
            return True
        path = self.task.state_path
        if choose or path is None:
            name = filedialog.asksaveasfilename(parent=self.root, defaultextension='.qrrx',
                                                filetypes=[('接收现场', '*.qrrx')],
                                                initialfile=self.task.parameters.task_id[:16] + '.qrrx')
            if not name:
                return False
            path = Path(name)
        try:
            self.task.save(path)
        except OSError as exc:
            self.saved = False
            self.refresh_task()
            self.report_error('接收现场保存失败', str(exc), phase='state_error')
            return False
        self.saved = True
        if self.phase == 'state_error':
            self.update_task()
        else:
            self.refresh_task()
        self.status.set('接收现场已保存。')
        self.append_log(self.event_text(self.task.events[-1]))
        return True

    def new(self):
        if self.worker is not None or not self.saved and not self.save():
            return
        self.task = None
        self.reset_request()
        self.show_idle()

    def open(self):
        if self.worker is not None or not self.saved and not self.save():
            return
        name = filedialog.askopenfilename(parent=self.root, filetypes=[('接收现场', '*.qrrx')])
        if not name:
            return
        try:
            task = ReceiveTask.load(Path(name))
        except (OSError, ValueError) as exc:
            self.report_error('接收现场打开失败', str(exc))
            return
        self.task, self.saved = task, True
        self.reset_request()
        self.scan.set('')
        for event in task.events[-30:]:
            self.append_log(self.event_text(event))
        self.update_task()
        if task.verified:
            self.save_output()

    def save_output(self):
        try:
            self.task.write_output(self.output_directory())
        except (OSError, ValueError) as exc:
            self.report_error('接收文件保存失败', str(exc), phase='error')
            return
        self.saved = False
        if self.save():
            self.update_task()

    def open_result(self):
        path = str(Path(self.task.output_path).resolve().parent)
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])
        except OSError as exc:
            self.report_error('打开结果目录失败', str(exc))

    def report_error(self, title, details, phase=None):
        self.append_log(f'{title}：{details}')
        if phase:
            self.set_state(phase, title, '详细原因见接收日志。', '处理失败。请查看接收日志。')
        else:
            self.status.set(f'{title}。详细原因见接收日志。')
        messagebox.showerror(title, details, parent=self.root)

    def refresh_task(self):
        task = self.task
        self.missing = task.missing
        self.show_counts(task.parameters.task_id[:12], task.done, task.parameters.num_chunks,
                         '现场已保存' if self.saved else '现场未保存')
        self.draw(cells(task))
        preview = '、'.join(f'#{index}' for index in self.missing[:5])
        if len(self.missing) > 5:
            preview += f' … 共 {len(self.missing):,} 块'
        self.missing_summary.set(preview)
        if task.verified:
            manifest = task.manifest
            values = (manifest['filename'], f"{manifest['size']:,} 字节", manifest['sha256'],
                      str(Path(task.output_path).resolve()) if task.output_path else '')
            for variable, value in zip(self.result_values, values):
                variable.set(value)
            self.result_button.state(['!disabled'] if task.output_path else ['disabled'])

    def show_counts(self, identity, done, total, state=''):
        self.counts.set(f'任务 {identity}   ·   已恢复 {done:,} / {total:,} 块' + (f'   ·   {state}' if state else ''))

    def update_task(self):
        if self.task is None:
            self.show_idle()
            return
        self.refresh_task()
        if self.task.integrity_error:
            self.set_state('error', '最终校验失败', '详细原因见接收日志。')
            self.append_log(self.task.integrity_error)
        elif self.task.verified and self.task.output_path:
            self.set_state('success', '文件校验通过并已保存')
        elif self.task.verified:
            self.set_state('error', '文件已校验，等待保存', '请保存到接收目录。')
        else:
            self.set_state('partial', '本轮解析结束', f'还有 {len(self.missing):,} 块待恢复。')

    def event_text(self, event):
        action = event['action']
        counts = (f"已恢复 {event['restored']:,}/{event['total']:,} 块，未恢复 {event['missing']:,} 块"
                  if 'total' in event else '')
        label = event.get('file', self.task.label)
        if action == '视频解析结束':
            return (f"{label} · {action} · {counts} · 本轮新增 {event['new_blocks']:,} 块。 "
                    f"{event['error'] or event['next_step']}")
        if action == '生成补传请求':
            return f"{label} · {counts} · 请求 {event['requested']:,} 块，{event['mode']}，{event['code_length']:,} 字符。"
        if action == '现场已保存':
            return f"{label} · {counts} · 现场已保存：{event['path']}"
        return f"{label} · {action} · {event.get('error', '')}"

    def draw(self, states):
        self.map_cells = states
        self.map_view.selected = self.selected
        self.map_view.set_cells(states)

    def select_cell(self, index):
        if self.worker is not None:
            return
        self.selected.symmetric_difference_update((index,))
        self.draw(self.map_cells)
        if self.scope.get() == '选中区域':
            self.clear_code()

    def cell_details(self, index):
        cell = self.map_cells[index]
        self.show_text(f'源块 #{cell.start}–#{cell.end - 1}',
                       f'已恢复 {cell.restored:,} / {cell.end - cell.start:,} 块。\n{cell.reason}\n\n'
                       '未恢复块编号\n' + (', '.join(map(str, cell.missing)) if cell.missing else '无'))

    def clear_selection(self):
        self.selected.clear()
        self.draw(self.map_cells)
        if self.scope.get() == '选中区域':
            self.clear_code()

    def reset_request(self):
        self.selected.clear()
        self.scope.set('整个任务')
        self.clear_code()

    def clear_code(self):
        self.set_code('', '尚未生成补传码')

    def set_code(self, code, summary):
        self.feedback_code = code
        self.code.set(code)
        self.code_summary.set(summary)
        for button in (self.view_code_button, self.copy_button, self.export_button):
            button.state(['!disabled'] if code else ['disabled'])

    def generate(self, selected=None):
        if self.worker is not None or self.task is None:
            return
        if selected is not None:
            self.scope.set('选中区域' if selected else '整个任务')
        scope = None
        mode = self.modes[self.mode.get()]
        if self.scope.get() == '选中区域':
            if not self.selected:
                self.status.set('请在源块恢复图中选择区域。')
                return
            if mode == 'all':
                self.status.set('“全部重发”仅适用于整个任务。')
                return
            scope = {block for index in self.selected
                     for block in range(self.map_cells[index].start, self.map_cells[index].end)}
        try:
            feedback = self.task.feedback(mode, scope)
        except ValueError as exc:
            self.report_error('补传请求生成失败', str(exc))
            return
        self.append_log(self.event_text(self.task.events[-1]))
        self.saved = False
        if self.task.verified and self.task.output_path is None:
            self.save_output()
        elif self.save():
            self.update_task()
        summary = f'请求 {len(feedback.requested):,} 块 · {len(feedback.code):,} 字符 · {feedback.description}'
        self.set_code(feedback.code, summary)
        if self.saved and feedback.requested:
            self.status.set('将补传码交给发送端，在原任务中校验并补传。')
        elif self.saved and self.missing:
            self.status.set('所选区域已恢复，可选择其他区域生成补传码。')

    def copy(self):
        if self.feedback_code:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.feedback_code)
            self.status.set(f'已复制 {len(self.feedback_code):,} 字符。请粘贴到发送端的原任务。')

    def export(self):
        if not self.feedback_code:
            return
        name = filedialog.asksaveasfilename(parent=self.root, defaultextension='.qrreq',
                                            filetypes=[('补传请求', '*.qrreq')])
        if name:
            try:
                atomic_write(Path(name), (self.feedback_code + '\n').encode('ascii'))
            except OSError as exc:
                self.report_error('补传请求导出失败', str(exc))
                return
            self.status.set('补传请求已导出。')
            self.append_log(f'补传请求已导出：{name}')

    def settings(self):
        if self.worker is not None:
            return
        window = tk.Toplevel(self.root)
        window.title('接收参数设置')
        window.transient(self.root)
        panel = ttk.Frame(window, padding=16)
        panel.pack(fill='both', expand=True)
        panel.columnconfigure(1, weight=1)
        output, fps, monitor = (tk.StringVar(value=variable.get()) for variable in (self.output, self.fps, self.monitor))
        for row, (label, value) in enumerate(zip(('接收目录', '录制帧率（帧/秒）', '屏幕编号'), (output, fps, monitor))):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=4)
            ttk.Entry(panel, textvariable=value, width=42 if row == 0 else 10).grid(
                row=row, column=1, sticky='ew' if row == 0 else 'w', pady=4)

        def browse():
            path = filedialog.askdirectory(parent=window)
            if path:
                output.set(path)

        ttk.Button(panel, text='浏览…', command=browse).grid(row=0, column=2, padx=(7, 0))
        ttk.Label(panel, text='应用本次仅改变当前窗口的参数。设为默认后，下次打开时也使用这些值。',
                  style='Muted.TLabel', wraplength=425).grid(row=3, column=0, columnspan=3, sticky='w', pady=(8, 10))

        def apply(persist=False):
            try:
                self.recording_values(fps.get(), monitor.get())
                path = self.output_directory(output.get())
                if persist:
                    save_defaults('receiver', {'output': str(path), 'fps': fps.get(), 'monitor': monitor.get()})
            except (OSError, ValueError) as exc:
                messagebox.showerror('接收参数设置失败', str(exc), parent=window)
                return
            self.output.set(str(path))
            self.fps.set(fps.get())
            self.monitor.set(monitor.get())
            self.status.set('已保存为接收默认参数。' if persist else '已应用本次接收参数。')
            window.destroy()

        buttons = ttk.Frame(panel)
        buttons.grid(row=4, column=0, columnspan=3, sticky='e')
        ttk.Button(buttons, text='取消', command=window.destroy).pack(side='left', padx=4)
        ttk.Button(buttons, text='设为默认', command=lambda: apply(True)).pack(side='left', padx=4)
        ttk.Button(buttons, text='应用本次', command=apply, style='Primary.TButton').pack(side='left', padx=(4, 0))
        window.bind('<Escape>', lambda _: window.destroy())
        window.grab_set()

    def output_directory(self, value=None):
        value = self.output.get() if value is None else value
        if not value.strip():
            raise ValueError('请选择接收目录。')
        return Path(value).resolve()

    @staticmethod
    def recording_values(fps, monitor):
        try:
            fps, monitor = float(fps), int(monitor)
        except ValueError as exc:
            raise ValueError('录制帧率需为 1–120，屏幕编号需为正整数。') from exc
        if not 1 <= fps <= 120 or monitor < 1:
            raise ValueError('录制帧率需为 1–120，屏幕编号需为正整数。')
        return fps, monitor

    def import_video(self, legacy=False):
        if self.worker is not None:
            return
        name = filedialog.askopenfilename(parent=self.root,
                                          title='旧视频一次性解析' if legacy else '导入接收视频',
                                          filetypes=[('视频', '*.mp4 *.mkv *.avi *.mov'), ('所有文件', '*')])
        if name:
            self.launch(Path(name), legacy=legacy)

    def record(self):
        if self.worker is not None:
            return
        try:
            recording = self.recording_values(self.fps.get(), self.monitor.get())
        except ValueError as exc:
            messagebox.showerror('录制参数有误', str(exc), parent=self.root)
            return
        name = filedialog.asksaveasfilename(parent=self.root, title='保存录制视频', defaultextension='.mp4',
                                            filetypes=[('MP4 视频', '*.mp4')])
        if name:
            self.launch(Path(name), recording=recording)

    def launch(self, video, legacy=False, recording=None):
        if self.worker is not None:
            return
        try:
            output = self.output_directory()
        except (OSError, ValueError) as exc:
            self.report_error('接收目录不可用', str(exc))
            return
        self.stop_event.clear()
        self.reset_request()
        self.append_log(f'{"开始录屏" if recording else "导入视频"}：{video}')
        task = self.task

        def work():
            stage = '屏幕录制失败' if recording else '视频解析失败'
            try:
                if recording:
                    last_update = 0.0

                    def recorded(count, elapsed):
                        nonlocal last_update
                        if elapsed - last_update >= 0.2:
                            self.events.put(('recording', (count, elapsed)))
                            last_update = elapsed

                    count, elapsed = record_screen(video, self.stop_event, *recording, progress=recorded, overwrite=True)
                    self.events.put(('log', f'录屏已保存：{video} · {count:,} 帧 · {elapsed:.1f} 秒'))
                    self.stop_event.clear()
                    if self.close_event.is_set():
                        self.events.put(('closed_recording', None))
                        return
                    self.events.put(('wait', None))
                    stage = '视频解析失败'
                if legacy:
                    last_update = 0.0
                    counts = (0, 0)

                    def legacy_progress(frame, total, _saver):
                        nonlocal last_update, counts
                        counts = (frame, total)
                        now = time.monotonic()
                        if now - last_update >= 0.1:
                            self.events.put(('scan', counts))
                            last_update = now

                    files = receive_legacy_video(video, output, self.stop_event, progress=legacy_progress)
                    self.events.put(('legacy', (files, counts)))
                else:
                    def progress(frame, total, current):
                        data = None if current is None else (current.parameters.task_id[:12],
                                                            current.done, current.parameters.num_chunks, cells(current))
                        self.events.put(('progress', (frame, total, data)))

                    result = process_video(video, task, output, self.stop_event, progress=progress)
                    self.events.put(('done', result))
            except (OSError, ValueError, cv2.error, ScreenShotError) as exc:
                self.events.put(('error', (stage, str(exc))))

        self.worker = threading.Thread(target=work)
        if recording:
            self.counts.set('录制时长 0.0 秒   ·   已录制 0 帧')
            self.scan.set('')
            self.set_state('recording', '正在录制屏幕', '停止后会保存 MP4 并开始解析。',
                           '切换到播放二维码的窗口，录够所需内容后停止录制。')
        else:
            self.waiting(legacy)
        self.worker.start()

    def waiting(self, legacy=False):
        self.counts.set('')
        self.scan_frames(0, 0)
        if legacy:
            self.set_state('legacy', '正在解析旧视频', '', '正在逐帧扫描旧格式二维码。')
        else:
            self.set_state('waiting', '等待首个有效包', '', '正在逐帧扫描视频中的 QT2 二维码。')

    def scan_frames(self, frame, total):
        self.scan.set(f'已扫描视频帧 {frame:,} / {total:,}' if total > 0 else f'已扫描 {frame:,} 帧')
        if self.phase == 'waiting' and total > 0:
            self.progress.configure(maximum=max(frame, total), value=frame)

    def stop(self):
        self.stop_event.set()
        if self.phase == 'recording':
            self.set_state('saving_recording', '正在保存录制视频', '', '保存完成后会自动解析。')
        else:
            self.cancel_wait()
            self.wait_indicator.grid_remove()
            self.stop_button.state(['disabled'])
            self.status.set('正在停止解析并保存接收现场。')

    def finish_round(self, result):
        self.task, self.saved = result.task, result.state_saved or result.task is None
        self.update_task()
        if not result.duplicate_video:
            self.scan_frames(result.frames, result.total_frames)
        self.append_log(result.text())
        if result.error:
            titles = {'scan': '视频解析失败', 'verify': '最终校验失败', 'output': '接收文件保存失败',
                      'state': '接收现场保存失败'}
            self.close_event.clear()
            phase = 'state_error' if result.error_stage == 'state' else 'error'
            self.report_error(titles[result.error_stage], result.error, phase=phase)
        elif result.cancelled:
            self.set_state('cancelled', '已停止解析',
                           '可追加视频继续接收。' if self.task else '可重新导入保存的视频。',
                           '已停止扫描。')
        elif result.duplicate_video:
            self.scan.set('')
            self.status.set('该视频已处理，现场保留原进度。')
        elif not result.matched:
            self.set_state('empty', '本轮未找到有效包',
                           '原任务进度已保留。' if self.task else '',
                           '请确认视频包含清晰、完整的发送端二维码。')

    def finish_legacy(self, files, counts):
        self.update_task()
        self.scan_frames(*counts)
        if self.stop_event.is_set():
            title = '已停止旧视频解析'
        elif files:
            title = '旧视频解析结束'
        else:
            title = '旧视频中未恢复文件'
        detail = f'已保存 {len(files):,} 个文件。'
        self.set_state('legacy_done', title, detail, detail)
        self.append_log(f'{title} · {detail}')
        for path in files:
            self.append_log(f'旧视频接收结果：{path}')

    def close(self):
        self.cancel_wait()
        self.wait_indicator.grid_remove()
        if self.worker is not None:
            self.close_event.set()
            self.stop_event.set()
            self.stop_button.state(['disabled'])
            self.status.set('正在停止并保存，处理完成后关闭。')
        elif self.saved or self.save():
            self.root.destroy()

    def poll(self):
        self.poll_after = None
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'recording':
                count, elapsed = value
                self.counts.set(f'录制时长 {elapsed:.1f} 秒   ·   已录制 {count:,} 帧')
            elif kind == 'wait':
                if not self.close_event.is_set():
                    self.waiting()
            elif kind == 'scan':
                self.scan_frames(*value)
            elif kind == 'progress':
                frame, total, data = value
                self.scan_frames(frame, total)
                if data is not None and not self.close_event.is_set():
                    identity, done, chunks, states = data
                    self.show_counts(identity, done, chunks)
                    self.draw(states)
                    if self.phase != 'parsing':
                        detail = '正在停止解析并保存接收现场。' if self.stop_event.is_set() else ''
                        self.set_state('parsing', '正在解析接收任务', detail)
                    self.progress.configure(maximum=chunks, value=done)
            elif kind == 'log':
                self.append_log(value)
            else:
                # 终态消息先于线程退出入队; join 只等线程从入队返回.
                self.worker.join()
                self.worker = None
                if kind == 'done':
                    self.finish_round(value)
                elif kind == 'legacy':
                    self.finish_legacy(*value)
                elif kind == 'error':
                    self.update_task()
                    self.close_event.clear()
                    self.report_error(*value, phase='error')
        if self.worker is None and self.close_event.is_set():
            if self.saved or self.save():
                self.root.destroy()
                return
            self.close_event.clear()
            self.update_task()
        self.poll_after = self.root.after(60, self.poll)


def main():
    root = tk.Tk()
    if '--self-test' in sys.argv:
        root.withdraw()
        ReceiverApp(root)
        root.update()
        root.destroy()
        return
    ReceiverApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
