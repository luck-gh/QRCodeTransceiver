"""用真实 Tk 验证缩放、滚动及键盘选择后的源块映射."""

import tkinter as tk
import unittest
from types import SimpleNamespace

from block_map import cells
from block_map_view import BlockMapView
from transfer_protocol import Parameters
from transfer_state import ReceiveTask


class BlockMapViewTests(unittest.TestCase):
    def setUp(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'当前环境无法启动 Tk: {error}')
        self.root = root
        self.addCleanup(root.destroy)
        root.geometry('760x320')
        colors = dict(bg='white', fg='black', border='black', muted='gray', accent='blue',
                      gray='gray', green='green', yellow='gold', red='red')
        theme = SimpleNamespace(colors=colors, on_change=lambda callback: None)
        self.selections = []
        self.details = []
        self.view = BlockMapView(root, theme, set(), self.selections.append, self.details.append)
        self.view.pack(fill='x')
        root.update()
        task = ReceiveTask(Parameters.read('ab' * 32, 1, 12345, 12345))
        self.states = cells(task)
        self.view.set_cells(self.states)
        root.update()

    def click(self, index, offset=1):
        view = self.view
        left, columns, step, _, _ = view._layout
        row, column = divmod(index, columns)
        x = left + column * step + offset
        origin = int(view._canvas.canvasy(0))
        y = max(2, row * view._row_height + 6 - origin)
        self.assertLess(y, view._canvas.winfo_height())
        view._click(SimpleNamespace(x=x, y=y))

    def test_resize_and_scroll_keep_the_original_group_index(self):
        view = self.view
        _, wide_columns, _, _, _ = view._layout
        self.click(130)
        self.root.geometry('410x320')
        self.root.update()
        _, narrow_columns, _, _, _ = view._layout
        self.assertLess(narrow_columns, wide_columns)
        self.assertIs(view.states, self.states)
        self.click(130)

        view._scroll('moveto', 1)
        self.click(999)
        self.assertEqual(self.selections, [130, 130, 999])
        last_caption = view._canvas.find_withtag('range')[-1]
        self.assertTrue(view._canvas.itemcget(last_caption, 'text').endswith('12344'))

    def test_keyboard_selects_and_opens_details_for_the_same_group(self):
        view = self.view
        view._focus(True)
        view._key(SimpleNamespace(keysym='End'))
        view._key(SimpleNamespace(keysym='space'))
        view._key(SimpleNamespace(keysym='Return'))
        self.assertEqual(self.selections, [999])
        self.assertEqual(self.details, [999])
        self.assertIn('#12332–12344', view._summary.cget('text'))

    def test_scale_and_small_task_numbers_are_readable_at_minimum_width(self):
        self.root.geometry('565x630')
        self.root.update()
        view = self.view
        self.assertIn('1000 档 · 每条 12–13 块', view._scale.cget('text'))
        for total, tier in ((93, 100), (10, 10)):
            task = ReceiveTask(Parameters.read('ab' * 32, 1, total, total))
            view.set_cells(cells(task))
            self.root.update()
            self.assertIn(f'{tier} 档 · 每条 1 块', view._scale.cget('text'))
        ids = view._canvas.find_withtag('source-number')
        self.assertEqual([view._canvas.itemcget(item, 'text') for item in ids], list(map(str, range(10))))
        self.click(9, offset=14)
        self.assertEqual(self.selections, [9])

    def test_constrained_height_preserves_summary_and_scrolls(self):
        self.root.geometry('565x630')
        view = self.view
        fixed = view.winfo_reqheight() - view._canvas.winfo_reqheight()
        view.pack_forget()
        view.place(x=20, y=0, width=525, height=fixed + view._row_height - 5)
        self.root.update()
        self.assertLess(view._canvas.winfo_height(), view._row_height)
        self.assertEqual(view._summary.winfo_height(), view._summary.winfo_reqheight())
        self.assertTrue(view._summary.winfo_ismapped())
        view._scroll('moveto', 1)
        self.click(999)
        self.assertEqual(self.selections, [999])
