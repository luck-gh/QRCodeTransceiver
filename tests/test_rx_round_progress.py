"""回归追加视频中的首包判定、实扫帧数和结果保存阶段."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from receive_pipeline import process_video
from transfer_protocol import Packet, SOURCE
from transfer_state import ReceiveTask, SendTask, file_digest


class RoundProgressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='qr-round-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        source = self.folder / 'payload.bin'
        source.write_bytes(bytes(range(256)))
        self.sender = SendTask.create(source, 128, False)
        self.task = ReceiveTask(self.sender.parameters)
        self.video = self.folder / 'frames.mp4'
        self.video.write_bytes(b'test scanner input')

    def packets(self, sender):
        return [Packet(sender.parameters, SOURCE, i, 1, sender.source(i)).text()
                for i in range(sender.parameters.num_chunks)]

    def run_round(self, frames, progress=None):
        with patch('receive_pipeline.VideoScanner') as factory, patch('receive_pipeline.time.monotonic', return_value=10):
            scanner = factory.return_value.__enter__.return_value
            scanner.total_frames = len(frames)

            def scan():
                for index, frame in enumerate(frames, 1):
                    scanner.frame_index = index
                    yield frame

            scanner.__iter__.return_value = scan()
            return process_video(self.video, self.task, self.folder / 'received', threading.Event(), progress=progress)

    def test_resumed_task_visible_at_first_matching_packet_even_when_duplicate(self):
        matching = self.packets(self.sender)[0]
        self.task.feed(matching)
        self.task.finish()
        source = self.folder / 'other.bin'
        source.write_bytes(b'other task')
        foreign = self.packets(SendTask.create(source, 128, False))[0]
        progress = []
        result = self.run_round([[foreign], [], [matching], []], lambda frame, total, task: progress.append(
            (frame, total, task is self.task, task.last_round_finished if task else None)))

        self.assertEqual(progress, [(1, 4, False, None), (3, 4, True, False)])
        self.assertEqual((result.frames, result.total_frames, result.new_blocks), (4, 4, 0))
        self.assertTrue(result.matched)
        self.assertTrue(result.state_saved)
        self.assertIn(file_digest(self.video), self.task.videos)

    def test_completed_task_scans_until_end_if_new_video_has_no_matching_packet(self):
        for packet in self.packets(self.sender):
            self.task.feed(packet)
        self.task.finish()
        result = self.run_round([[], [], []])

        self.assertEqual((result.frames, result.total_frames), (3, 3))
        self.assertFalse(result.matched)
        self.assertFalse(result.duplicate_video)
        self.assertTrue(result.task.verified)
        self.assertNotIn(file_digest(self.video), self.task.videos)

    def test_result_write_error_keeps_verified_data_and_saved_resume_state(self):
        frames = [[packet] for packet in self.packets(self.sender)]
        with patch.object(ReceiveTask, 'write_output', side_effect=OSError('disk full')):
            result = self.run_round(frames)

        self.assertEqual(result.error_stage, 'output')
        self.assertEqual(result.error, 'disk full')
        self.assertTrue(result.task.verified)
        self.assertTrue(result.state_saved)
        self.assertIsNone(result.task.output_path)


if __name__ == '__main__':
    unittest.main()
