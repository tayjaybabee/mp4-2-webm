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

    def convert_to_gif(self, in_path: str, out_path: str, progress_cb, four_chan_safe: bool):
        """Two-pass GIF creation with optional 4chan-safe constraints."""

        palette_path = out_path + "_palette.png"

        # Keep GIFs modest in size when targeting 4chan
        gif_filters = ["fps=10"]
        if four_chan_safe:
            gif_filters.append("scale='min(720,iw)':-2")

        filter_chain = ",".join(gif_filters)

        # ---- PASS 1: palette generation ----
        process1 = subprocess.Popen(
            [
                "ffmpeg",
                "-i",
                in_path,
                "-vf",
                f"{filter_chain},palettegen",
                palette_path,
                "-progress",
                "pipe:1",
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
                "-i",
                in_path,
                "-i",
                palette_path,
                "-lavfi",
                f"{filter_chain},paletteuse",
                out_path,
                "-progress",
                "pipe:1",
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
    def convert_async(self, in_path: str, out_path: str, progress_cb, four_chan_safe: bool):
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
        vf_filters = []
        ffmpeg_args = [
            "ffmpeg",
            "-i",
            in_path,
            "-c:v",
            "libvpx-vp9",
        ]

        if four_chan_safe:
            vf_filters.append("scale='min(1280,iw)':-2")
            ffmpeg_args.extend(["-crf", "32", "-b:v", "0", "-deadline", "realtime", "-fs", "6144k"])
        else:
            ffmpeg_args.extend(["-crf", str(self.quality_crf), "-b:v", "0"])  # VBR mode

        if vf_filters:
            ffmpeg_args.extend(["-vf", ",".join(vf_filters)])

        ffmpeg_args.extend(
            [
                "-c:a",
                "libopus",
                "-b:a",
                f"{self.audio_kbps}k",
                "-progress",
                "pipe:1",
                "-nostats",
                out_path,
            ]
        )

        process = subprocess.Popen(
            ffmpeg_args,
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

    # ---------------------------------------------------------------------
    def _centered_video(self, path: str):
        """Display a video at a consistent medium width."""

        left, center, right = st.columns([1, 2, 1])
        with center:
            st.video(path)

    # -------------------------------------------------------------------------
    def render(self):
        st.set_page_config(page_title="MP4 → WebM Converter", layout="wide")

        st.title("🎬 MP4 → WebM Converter (VP9 + Opus • High Quality)")
        st.write("Upload an MP4 file, inspect metadata, and convert with live progress.")

        uploads = st.file_uploader(
            "Upload one or more MP4 files", type=["mp4"], accept_multiple_files=True
        )

        convert_all = False
        if uploads:
            convert_all = st.button(
                "Convert ALL uploads", type="primary", help="Run conversions for every file below"
            )

        if not uploads:
            return

        for idx, uploaded in enumerate(uploads):
            with st.expander(f"{uploaded.name} — click to view controls", expanded=False):
                st.subheader("🎞️ Source Preview")

                # Write input to temp file per upload
                tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tmp_in.write(uploaded.getbuffer())
                tmp_in.flush()

                self._centered_video(tmp_in.name)

                meta = self.converter.get_metadata(tmp_in.name)

                st.markdown("**📊 Metadata**")
                st.json(meta)

                col1, col2 = st.columns(2)
                with col1:
                    gif_mode = st.checkbox(
                        "🖼️ Export as GIF instead of WebM",
                        key=f"gif_mode_{idx}",
                    )
                with col2:
                    four_chan_safe = st.checkbox(
                        "✅ Keep output 4chan-friendly (≤6 MB, scaled)",
                        key=f"chan_safe_{idx}",
                        help="Downscales and compresses to stay under 6 MB where possible.",
                    )

                convert_button = st.button(
                    "Convert to GIF" if gif_mode else "Convert to WebM",
                    key=f"convert_{idx}",
                )

                if convert_all or convert_button:
                    base = uuid.uuid4().hex
                    ext = "gif" if gif_mode else "webm"
                    out_path = str(Path(tempfile.gettempdir()) / f"{base}.{ext}")

                    progress_placeholder = st.empty()
                    progress = progress_placeholder.progress(0)
                    status = st.empty()

                    def update(pct):
                        progress.progress(pct)

                    status.write("Converting… hold tight…")

                    if gif_mode:
                        self.converter.convert_to_gif(tmp_in.name, out_path, update, four_chan_safe)
                    else:
                        self.converter.convert_async(tmp_in.name, out_path, update, four_chan_safe)

                    status.write("✅ Done!")

                    if ext == "gif":
                        st.subheader("🖼️ Result Preview")
                        st.image(out_path)
                    else:
                        st.subheader("🎬 Result Preview")
                        self._centered_video(out_path)

                    output_size = Path(out_path).stat().st_size
                    size_mb = output_size / (1024 * 1024)
                    st.caption(f"Output size: {size_mb:.2f} MB")
                    if four_chan_safe and size_mb > 6:
                        st.warning("Output is still over 6 MB. Consider trimming duration or lowering resolution.")

                    with open(out_path, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download {ext.upper()}",
                            data=f,
                            file_name=f"{base}.{ext}",
                            mime="image/gif" if gif_mode else "video/webm",
                            key=f"download_{idx}",
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
