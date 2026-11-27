"""后台发送使用冻结任务, 只生成补传所需源块, 取消可终止有界队列."""

import queue
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from qrcode_tx_gui import SenderApp
from repair_request import create
from transfer_protocol import FOUNTAIN, Packet, SOURCE
from transfer_state import SendTask


class SenderWorkerTests(unittest.TestCase):
    def test_background_sender_targets_exact_sources_and_stops(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            source = Path(temp) / 'hello.txt'
            source.write_bytes(b'Hello from sender UI')
            task = SendTask.create(source, 128)
            task.save(Path(temp) / 'send.qrtx')
            frames, events, stop = queue.Queue(maxsize=2), queue.Queue(), threading.Event()
            with patch('qrcode_tx_gui.build_qr', side_effect=lambda text, *args: text):
                worker = threading.Thread(target=SenderApp._produce, args=(task, (1,), 400, frames, events, stop))
                worker.start()
                text, metadata, offset, total = frames.get(timeout=10)
                stop.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(events.empty())
            packet = Packet.read(text)
            self.assertEqual(packet, metadata)
            self.assertEqual((packet.number, packet.data, offset, total), (1, task.source(1), 1, 1))

    def test_complete_round_labels_source_and_repair_packets(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            path = Path(temp) / 'input.bin'
            path.write_bytes(b'x')
            task = SendTask.create(path)
            frames, events, stop = queue.Queue(maxsize=2), queue.Queue(), threading.Event()
            with patch('qrcode_tx_gui.build_qr', side_effect=lambda code, *args: code):
                worker = threading.Thread(target=SenderApp._produce, args=(task, None, 400, frames, events, stop))
                worker.start()
                try:
                    first = frames.get(timeout=10)
                    last = frames.get(timeout=10)
                    following = frames.get(timeout=10)
                finally:
                    stop.set()
                    worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(events.empty())
            self.assertEqual((first[1].kind, first[1].number, *first[2:]), (SOURCE, 0, 1, 2))
            self.assertEqual((last[1].kind, last[1].round_no, *last[2:]), (FOUNTAIN, first[1].round_no, 2, 2))
            self.assertEqual((following[1].kind, following[1].round_no, *following[2:]),
                             (SOURCE, first[1].round_no + 1, 1, 2))

    def test_render_failure_is_reported(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            path = Path(temp) / 'input.bin'
            path.write_bytes(b'x')
            frames, events = queue.Queue(maxsize=2), queue.Queue()
            with patch('qrcode_tx_gui.build_qr', side_effect=ValueError('QR too dense')):
                SenderApp._produce(SendTask.create(path), None, 400, frames, events, threading.Event())
            self.assertEqual(events.get_nowait()[0], 'play_error')
            self.assertTrue(frames.empty())


class SenderWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk 需要可用显示环境: {exc}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.source = self.folder / 'input.bin'
        self.source.write_bytes(b'x' * 512)
        self.task = SendTask.create(self.source, 128, False)
        with patch('qrcode_tx_gui.load_defaults', return_value={}):
            self.app = SenderApp(self.root, self.task)

    def wait_for_worker(self):
        deadline = time.monotonic() + 10
        while self.app.worker is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertIsNone(self.app.worker)

    def test_repair_requires_fresh_matching_request_before_sending(self):
        app = self.app
        params = self.task.parameters
        feedback = create(params.task_id, params.num_chunks, [1, 3])
        app.toggle_mode()
        self.assertTrue(app.start_button.instate(['disabled']))
        app.code.set(feedback.code)
        app.apply_request()
        self.assertEqual(app.requested, (1, 3))
        self.assertEqual(app.block_preview.get(), '#1、#3')
        self.assertEqual(app.payload_summary.get(), '2 块 / 256 字节块载荷')
        self.assertTrue(app.start_button.instate(['!disabled']))
        app.code.set(feedback.code + 'x')
        self.assertIsNone(app.requested)
        self.assertTrue(app.start_button.instate(['disabled']))
        with patch('qrcode_tx_gui.messagebox.showerror') as error:
            app.apply_request()
        error.assert_called_once()
        app.start()
        self.assertIsNone(app.player)
        self.assertIsNone(app.worker)
        self.assertTrue(app.repair_mode)

        request = self.folder / 'in.qrreq'
        request.write_text(feedback.code + '\n', encoding='utf-8')
        with patch('qrcode_tx_gui.filedialog.askopenfilename', return_value=str(request)):
            app.import_request()
        self.assertEqual(app.requested, (1, 3))
        new_task = SendTask.create(self.source, 256, False)
        new_path = self.folder / 'other.qrtx'
        new_task.save(new_path)
        with patch('qrcode_tx_gui.filedialog.askopenfilename', return_value=str(new_path)):
            app.open()
        self.assertTrue(app.repair_mode)
        self.assertEqual(app.code.get(), '')
        self.assertIsNone(app.requested)
        self.assertTrue(app.start_button.instate(['disabled']))
        app.toggle_mode()
        self.assertFalse(app.repair_mode)
        self.assertTrue(app.start_button.instate(['!disabled']))

    def test_creation_uses_selected_block_size_and_compression(self):
        app = self.app
        original = self.task.parameters
        app.code.set(create(original.task_id, original.num_chunks, [1]).code)
        app.apply_request()
        self.assertTrue(app.repair_mode)
        self.assertEqual(app.requested, (1,))
        with patch('qrcode_tx_gui.filedialog.askopenfilename', return_value=str(self.source)):
            app.new()
        app.parameters['chunk_size'].set('97')
        app.parameters['compress'].set('1')
        path = self.folder / 'frozen.qrtx'
        with patch('qrcode_tx_gui.filedialog.asksaveasfilename', return_value=str(path)):
            app.freeze()
        self.wait_for_worker()
        self.assertIsNone(app.pending_source)
        self.assertFalse(app.repair_mode)
        self.assertIsNone(app.requested)
        self.assertEqual(app.code.get(), '')
        self.assertTrue(app.start_button.instate(['!disabled']))
        self.assertEqual(app.task.parameters.chunk_size, 97)
        self.assertEqual(app.task.manifest['compression'], 'zlib')
        self.assertEqual(SendTask.load(path).parameters, app.task.parameters)
        app.parameters['chunk_size'].set('101')
        app.parameters['compress'].set('0')
        self.assertEqual(app.task.parameters.chunk_size, 97)
        self.assertIn('zlib', app.frozen_summary.get())

    def test_overflow_keeps_actions_reachable_in_a_short_window(self):
        app = self.app
        params = self.task.parameters
        with patch.object(self.root, 'winfo_screenheight', return_value=560):
            self.root.geometry('530x460')
            self.root.deiconify()
            app.code.set(create(params.task_id, params.num_chunks, [1]).code)
            app.apply_request()
            app.toggle_playback()
            self.root.update()
            self.assertEqual(self.root.minsize()[1], 460)
            self.assertTrue(app.workflow_scroll.winfo_ismapped())
            app.viewport.yview_moveto(1)
            self.root.update()
            bottom = app.viewport.winfo_rooty() + app.viewport.winfo_height()
            self.assertLessEqual(app.mode_button.winfo_rooty() + app.mode_button.winfo_height(), bottom)
            self.assertGreaterEqual(app.start_button.winfo_rooty(), app.viewport.winfo_rooty())
            self.assertTrue(app.start_button.instate(['!disabled']))

    def test_applying_parameters_and_saving_defaults_are_distinct(self):
        app = self.app
        size = min(self.root.winfo_screenheight() - 160, self.root.winfo_screenwidth() - 80, 400)
        app.parameters['size'].set(str(size))

        def edit_interval_and_click(value, action):
            app.settings()
            dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
            body, = dialog.winfo_children()
            frames = [child for child in body.winfo_children() if child.winfo_class() == 'TFrame']
            _, playback, buttons = frames
            interval = next(child for child in playback.winfo_children() if child.winfo_class() == 'TEntry')
            self.root.setvar(interval.cget('textvariable'), value)
            next(child for child in buttons.winfo_children() if child.cget('text') == action).invoke()
            return dialog

        with patch('qrcode_tx_gui.save_defaults') as save:
            edit_interval_and_click('220', '应用本次')
            save.assert_not_called()
            self.assertEqual(app.parameters['interval'].get(), '220')
            dialog = edit_interval_and_click('320', '保存为默认值')
            role, values = save.call_args.args
            self.assertEqual(role, 'sender')
            self.assertEqual(values['interval'], '320')
            self.assertEqual(app.parameters['interval'].get(), '220')
            dialog.destroy()


if __name__ == '__main__':
    unittest.main()
