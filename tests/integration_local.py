"""Exercise real yt-dlp and FFmpeg with generated audio served on localhost."""

import functools
import math
import struct
import sys
import tempfile
import threading
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ytdlp_studio import backend as app
from ytdlp_studio.inspection import inspect_link


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="studio-audio-test-") as folder:
        root = Path(folder)
        with wave.open(str(root / "sample.wav"), "wb") as audio:
            audio.setparams((1, 2, 44100, 44100, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * index / 44100))) for index in range(44100)))
        handler = functools.partial(QuietHandler, directory=str(root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = app.Settings(output_dir=str(root / "library"), write_archive=False, embed_thumbnail=False, sponsorblock=False,
                                    sync_enabled=True, sync_dir=str(root / "mirror"), sync_match_output_folder=False)
            job = app.build_jobs([f"http://127.0.0.1:{server.server_port}/sample.wav"], settings)[0]
            output = []
            preview = inspect_link(job.urls[0], settings, threading.Event())
            assert preview["title"], preview
            assert not (root / "library").exists(), "Preview must not create download output"
            code = app.run_download_job(job, output.append)
            assert code == 0, "".join(output)
            files = list((root / "library").glob("*.mp3"))
            assert len(files) == 1, "".join(output)
            from mutagen.mp3 import MP3
            assert MP3(files[0]).info.length > 0.5
            assert app.sync_output_dir(settings, output.append) == 0
            assert (root / "mirror" / files[0].name).read_bytes() == files[0].read_bytes()
            print("PASS: real yt-dlp download, bundled FFmpeg MP3 conversion, playable audio, mirror copy")
            failed = app.build_jobs([f"http://127.0.0.1:{server.server_port}/missing.wav", job.urls[0]], settings)[0]
            assert app.run_download_job(failed, output.append) != 0, "A failed item must not report success"
            print("PASS: metadata-only preview and native download failure reporting")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
