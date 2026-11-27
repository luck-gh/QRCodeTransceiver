"""接收端窄条源块恢复图及其选择交互."""

import tkinter as tk
from tkinter import font, ttk


class BlockMapView(ttk.Frame):
    """绘制源块快照.

    theme 提供颜色与主题通知, selected 保存选中的 Cell 索引.
    on_select 和 on_details 接收单个 Cell 索引.
    """

    BAR_STEP = 6
    MAX_COLUMNS = 100
    VISIBLE_ROWS = 10
    LEGEND = (
        ('gray', '未恢复'),
        ('green', '已恢复'),
        ('yellow', '部分恢复'),
        ('red', '待补或冲突'),
    )

    def __init__(self, parent, theme, selected, on_select, on_details):
        super().__init__(parent)
        self.theme = theme
        self.selected = selected
        self.on_select = on_select
        self.on_details = on_details
        self.states = []
        self._font = font.nametofont('TkDefaultFont', root=self)
        self._row_height = max(16, self._font.metrics('linespace') + 3)
        self._layout = None
        self._boxes = []
        self._displayed = []
        self._focused = False
        self._cursor = 0
        self._hover_position = None

        legend = ttk.Frame(self)
        legend.pack(anchor='w')
        self._swatches = []
        for color, caption in self.LEGEND:
            group = ttk.Frame(legend)
            group.pack(side='left', padx=(0, 9))
            swatch = tk.Canvas(group, width=9, height=12, highlightthickness=0, borderwidth=0)
            swatch.pack(side='left', padx=(0, 3))
            swatch.create_rectangle(0, 1, 9, 11, width=0, tags='fill')
            self._swatches.append((color, swatch))
            ttk.Label(group, text=caption).pack(side='left')
        self._scale = ttk.Label(self, width=1, anchor='w', style='Muted.TLabel')
        self._scale.pack(fill='x')
        self._summary = ttk.Label(self, width=1, anchor='w', style='Muted.TLabel')
        self._summary.pack(side='bottom', fill='x')

        graph = ttk.Frame(self)
        graph.pack(fill='both', expand=True, pady=(4, 2))
        self._canvas = tk.Canvas(graph, width=1, height=self._row_height, highlightthickness=1,
                                 borderwidth=0, yscrollincrement=1, takefocus=False)
        self._scrollbar = ttk.Scrollbar(graph, orient='vertical', command=self._scroll)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side='right', fill='y')
        self._canvas.pack(side='left', fill='both', expand=True)

        self._canvas.bind('<Configure>', lambda event: self._render())
        self._canvas.bind('<Motion>', self._motion)
        self._canvas.bind('<Leave>', self._leave)
        self._canvas.bind('<Button-1>', self._click)
        self._canvas.bind('<Double-Button-1>', self._details)
        self._canvas.bind('<MouseWheel>', self._wheel)
        self._canvas.bind('<Button-4>', lambda event: self._scroll('scroll', -1, 'units'))
        self._canvas.bind('<Button-5>', lambda event: self._scroll('scroll', 1, 'units'))
        self._canvas.bind('<FocusIn>', lambda event: self._focus(True))
        self._canvas.bind('<FocusOut>', lambda event: self._focus(False))
        self._canvas.bind('<KeyPress>', self._key)
        theme.on_change(self._apply_theme)
        self._apply_theme()

    @property
    def minimum_height(self):
        """返回图例、说明和最多两行窄条的布局高度."""
        columns = self._layout[1] if self._layout is not None else self.MAX_COLUMNS
        rows = 2 if len(self.states) > columns else 1
        surrounding = self.winfo_reqheight() - self._canvas.winfo_reqheight()
        return surrounding + rows * self._row_height + 2

    def set_cells(self, states):
        """绘制 states 中的 Cell 快照, 保持原有分组和索引."""
        if len(states) != len(self.states) or (states and states[-1].end != self.states[-1].end):
            self._cursor = 0
            self._hover_position = None
            self._canvas.yview_moveto(0)
        self.states = states
        self._canvas.configure(takefocus=bool(states))
        self._update_scale()
        self._render()

    def _update_scale(self):
        text = '黄色表示组内部分源块已恢复'
        if self.states:
            total = self.states[-1].end
            tier = 10 if total <= 10 else 100 if total <= 100 else 1000
            size, remainder = divmod(total, len(self.states))
            coverage = f'{size}–{size + 1}' if remainder else str(size)
            text = f'{tier} 档 · 每条 {coverage} 块 · {text}'
        self._scale.configure(text=text)

    def _apply_theme(self):
        colors = self.theme.colors
        self._canvas.configure(background=colors['bg'], highlightbackground=colors['border'],
                               highlightcolor=colors['accent'])
        for color, swatch in self._swatches:
            swatch.configure(background=colors['bg'])
            swatch.itemconfigure('fill', fill=colors[color])
        self._displayed = []
        self._canvas.itemconfigure('range', fill=colors['muted'])
        self._canvas.itemconfigure('source-number', fill=colors['fg'])
        self._render()

    def _render(self):
        width = self._canvas.winfo_width()
        if width <= 1:
            return
        total = self.states[-1].end if self.states else 0
        numbered = 0 < total <= 10
        last = max(0, total - 1)
        # 预留最大块号的宽度, 所有行从同一列开始.
        left = 5 if numbered else self._font.measure(f'{last}–{last}') + 13
        step = self._font.measure('00') + 23 if numbered else self.BAR_STEP
        columns = min(self.MAX_COLUMNS, max(1, (width - left - 3) // step))
        rows = max(1, (len(self.states) + columns - 1) // columns)
        layout = (left, columns, step, len(self.states), total)
        if self._layout != layout:
            self._layout = layout
            self._canvas.delete('all')
            self._canvas.configure(height=min(rows, self.VISIBLE_ROWS) * self._row_height,
                                   scrollregion=(0, 0, 1, rows * self._row_height))
            self._boxes = []
            self._displayed = []
            for index, cell in enumerate(self.states):
                row, column = divmod(index, columns)
                x, y = left + column * step, row * self._row_height
                bar_edge = x + step - 2
                if numbered:
                    bar_edge = x + 10
                    self._canvas.create_text(x + 13, y + self._row_height // 2, anchor='w',
                                             text=str(cell.start), font=self._font, tags='source-number',
                                             fill=self.theme.colors['fg'])
                elif column == 0:
                    end = self.states[min(index + columns, len(self.states)) - 1].end
                    caption = str(cell.start) if cell.start == end - 1 else f'{cell.start}–{end - 1}'
                    self._canvas.create_text(left - 6, y + self._row_height // 2, anchor='e',
                                             text=caption, font=self._font, tags='range',
                                             fill=self.theme.colors['muted'])
                self._boxes.append((
                    self._canvas.create_rectangle(x, y + 4, bar_edge,
                                                  y + self._row_height - 3, width=0),
                    self._canvas.create_line(x - 1, y + 2, x + step - 1, y + 2, width=2),
                    self._canvas.create_line(x - 1, y + self._row_height - 1,
                                             x + step - 1, y + self._row_height - 1, width=2),
                ))
            if self._focused and self.states:
                self._reveal_cursor()
        self._update_boxes()
        self._update_summary()

    def _update_boxes(self):
        colors = self.theme.colors
        displayed = []
        for index, (bar, marker, cursor) in enumerate(self._boxes):
            status = (self.states[index].color, index in self.selected,
                      self._focused and index == self._cursor)
            displayed.append(status)
            previous = self._displayed[index] if self._displayed else (None, None, None)
            if status[0] != previous[0]:
                self._canvas.itemconfigure(bar, fill=colors[status[0]])
            if status[1] != previous[1]:
                self._canvas.itemconfigure(marker, fill=colors['fg'], state='normal' if status[1] else 'hidden')
            if status[2] != previous[2]:
                self._canvas.itemconfigure(cursor, fill=colors['accent'], state='normal' if status[2] else 'hidden')
        self._displayed = displayed

    def _index_at(self, x, y):
        left, columns, step, _, _ = self._layout
        x = int(self._canvas.canvasx(x))
        y = int(self._canvas.canvasy(y))
        if x < left or y < 0:
            return None
        column = (x - left) // step
        index = y // self._row_height * columns + column
        if column < columns and index < len(self.states):
            return index
        return None

    def _update_summary(self):
        index = self._index_at(*self._hover_position) if self._hover_position is not None else None
        if index is None and self._focused and self.states:
            index = self._cursor
        if index is not None:
            cell = self.states[index]
            label = f'#{cell.start}' if cell.start + 1 == cell.end else f'#{cell.start}–{cell.end - 1}'
            text = f'{label}  已恢复 {cell.restored}/{cell.end - cell.start} · {cell.reason}'
        else:
            text = f'已选 {len(self.selected)} 组 · 单击或空格选择，双击或回车查看'
        self._summary.configure(text=text)

    def _motion(self, event):
        self._hover_position = event.x, event.y
        self._update_summary()

    def _leave(self, event):
        self._hover_position = None
        self._update_summary()

    def _click(self, event):
        index = self._index_at(event.x, event.y)
        if index is not None:
            self._cursor = index
            self._canvas.focus_set()
            self.on_select(index)

    def _details(self, event):
        index = self._index_at(event.x, event.y)
        if index is not None:
            self.on_details(index)

    def _scroll(self, *args):
        if args[0] == 'scroll' and args[2] == 'units':
            step = min(self._row_height, max(1, self._canvas.winfo_height() - 2))
            args = ('scroll', int(args[1]) * step, 'units')
        self._canvas.yview(*args)
        self._update_summary()

    def _wheel(self, event):
        self._scroll('scroll', -1 if event.delta > 0 else 1, 'units')

    def _focus(self, focused):
        self._focused = focused
        if focused and self.states:
            self._reveal_cursor()
        self._update_boxes()
        self._update_summary()

    def _reveal_cursor(self):
        _, columns, _, _, _ = self._layout
        row = self._cursor // columns
        top = row * self._row_height
        bottom = top + self._row_height
        first = int(self._canvas.canvasy(1))
        height = self._canvas.winfo_height() - 2
        extent = ((len(self.states) - 1) // columns + 1) * self._row_height
        if height < self._row_height:
            self._canvas.yview_moveto((top + (self._row_height - height) // 2) / extent)
        elif top < first:
            self._canvas.yview_moveto(top / extent)
        elif bottom > first + height:
            self._canvas.yview_moveto((bottom - height) / extent)

    def _key(self, event):
        if not self.states:
            return
        _, columns, _, _, _ = self._layout
        moves = {
            'Left': self._cursor - 1, 'Right': self._cursor + 1,
            'Up': self._cursor - columns, 'Down': self._cursor + columns,
            'Home': 0, 'End': len(self.states) - 1,
        }
        if event.keysym in moves:
            self._hover_position = None
            self._cursor = min(len(self.states) - 1, max(0, moves[event.keysym]))
            self._reveal_cursor()
            self._update_boxes()
            self._update_summary()
        elif event.keysym == 'space':
            self.on_select(self._cursor)
        elif event.keysym in ('Return', 'KP_Enter'):
            self.on_details(self._cursor)
        else:
            return
        return 'break'
