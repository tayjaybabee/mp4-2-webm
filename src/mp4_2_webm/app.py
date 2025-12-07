import streamlit as st
import tempfile
import subprocess
import time
import uuid
import json
import ffmpeg
from pathlib import Path


# =============================================================================
#   VideoConverter — the brains of this operation
# =============================================================================

class VideoConverter:
    """
Handles:
- metadata extraction
- clean, high-quality VP9 conversion
- non-blocking progress reporting
"""

    def __init__(self, quality_crf=18, audio_kbps=192):
        self.quality_crf = quality_crf
        self.audio_kbps = audio_kbps

    # -------------------------------------------------------------------------
    def get_metadata(self, path: str) -> dict:
        """Use ffprobe (via ffmpeg-python) to extract metadata."""
        try:
            return ffmpeg.probe(path)
        except ffmpeg.Error:
            return {}

    def convert_to_gif(self, in_path: str, out_path: str, progress_cb):
        """Two-pass high-quality GIF creation with progress updates."""

        palette_path = out_path + "_palette.png"

        # ---- PASS 1: palette generation ----
        process1 = subprocess.Popen(
            [
                "ffmpeg",
                "-i", in_path,
                "-vf", "fps=10,palettegen",
                palette_path,
                "-progress", "pipe:1",
                "-nostats",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        )

        for line in process1.stdout:
            line = line.strip()
            if line.startswith("out_time_ms"):
                value = line.split("=")[1]
                if value.isdigit():
                    pct = (int(value) / 1000000) * 50  # palette gen counts ~half
                    progress_cb(min(int(pct), 50))

        process1.wait()

        # ---- PASS 2: apply palette and generate GIF ----
        process2 = subprocess.Popen(
            [
                "ffmpeg",
                "-i", in_path,
                "-i", palette_path,
                "-lavfi", "fps=10,paletteuse",
                out_path,
                "-progress", "pipe:1",
                "-nostats",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        )

        for line in process2.stdout:
            line = line.strip()
            if line.startswith("out_time_ms"):
                value = line.split("=")[1]
                if value.isdigit():
                    pct = 50 + (int(value) / 1000000) * 50
                    progress_cb(min(int(pct), 100))

        process2.wait()

        # Cleanup
        try:
            Path(palette_path).unlink()
        except:
            pass


    # -------------------------------------------------------------------------
    def convert_async(self, in_path: str, out_path: str, progress_cb):
        """
        Convert MP4 → WebM (VP9/Opus) using ffmpeg.
        Runs asynchronously using a thread and progress pipes.
        """

        # Step 1 — get duration
        meta = self.get_metadata(in_path)
        try:
            duration = float(meta["format"]["duration"])
        except:
            duration = None

        total_ms = duration * 1000 if duration else None

        # Step 2 — launch ffmpeg
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-i", in_path,
                "-c:v", "libvpx-vp9",
                "-crf", str(self.quality_crf),
                "-b:v", "0",                   # VBR mode
                "-c:a", "libopus",
                "-b:a", f"{self.audio_kbps}k",
                "-progress", "pipe:1",
                "-nostats",
                out_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1,
            universal_newlines=True,
        )

        # Step 3 — read progress inline (Streamlit callbacks must run in-session)
        for line in process.stdout:
            line = line.strip()

            if total_ms and line.startswith("out_time_ms"):
                value = line.split("=")[1]

                # Skip N/A or other invalid values
                if not value.isdigit():
                    continue

                ms = int(value)
                pct = (ms / total_ms) * 100
                progress_cb(min(int(pct), 100))

        process.stdout.close()

        # Step 4 — wait for completion without blocking UI
        while process.poll() is None:
            time.sleep(0.1)

        progress_cb(100)


# =============================================================================
#   Streamlit UI King (or Queen)
# =============================================================================

class StreamlitUI:
    """
Owns:
- layout
- widgets
- user interaction
- calling VideoConverter
"""

    def __init__(self):
        self.converter = VideoConverter()

    # -------------------------------------------------------------------------
    def render(self):
        st.set_page_config(page_title="MP4 → WebM Converter", layout="wide")

        st.title("🎬 MP4 → WebM Converter (VP9 + Opus • High Quality)")
        st.write("Upload an MP4 file, inspect metadata, and convert with live progress.")

        uploaded = st.file_uploader("Upload your MP4", type=["mp4"])

        if not uploaded:
            return

        # Write input to temp file
        tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        tmp_in.write(uploaded.read())
        tmp_in.flush()

        # ---------------------------------------------------------------------
        st.subheader("🎞️ Source Preview")
        st.video(tmp_in.name)

        # ---------------------------------------------------------------------
        st.subheader("📊 Metadata")
        meta = self.converter.get_metadata(tmp_in.name)
        st.json(meta)

        gif_mode = st.checkbox("🖼️ Export as GIF instead of WebM")
        convert_button = st.button('Convert' if not gif_mode else 'Convert to gif')

        # ---------------------------------------------------------------------
        if convert_button:
            base = uuid.uuid4().hex
            ext = "gif" if gif_mode else "webm"
            out_path = str(Path(tempfile.gettempdir()) / f"{base}.{ext}")

            progress = st.progress(0)
            status = st.empty()

            def update(pct):
                progress.progress(pct)

            status.write("Converting… hold tight…")

            if gif_mode:
                self.converter.convert_to_gif(tmp_in.name, out_path, update)
            else:
                self.converter.convert_async(tmp_in.name, out_path, update)

            status.write("✅ Done!")

            if ext == "gif":
                st.subheader("🖼️ Result Preview")
                st.image(out_path)
            else:
                st.subheader("🎬 Result Preview")
                st.video(out_path)

            with open(out_path, "rb") as f:
                st.download_button(
                    label=f"⬇️ Download {ext.upper()}",
                    data=f,
                    file_name=f"{base}.{ext}",
                    mime="image/gif" if gif_mode else "video/webm",
                )


# =============================================================================
#   Entry Point for Poetry Script
# =============================================================================

def run():
    """Launch Streamlit app programmatically."""
    import subprocess
    subprocess.run(["streamlit", "run", __file__])


# Standard Python CLI entry
if __name__ == "__main__":
    ui = StreamlitUI()
    ui.render()
