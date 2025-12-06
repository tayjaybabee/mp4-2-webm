# file: app.py
import os
import uuid
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template,
    send_from_directory
)
import ffmpeg

# --- Configuration ------------------------------------------------------------

BASE = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE, 'converted')
ALLOWED = {'mp4'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER



# --- Helpers -----------------------------------------------------------------

def allowed_file(fname: str) -> bool:
    """Return True if filename is an allowed MP4."""
    return '.' in fname and fname.rsplit('.', 1)[1].lower() in ALLOWED


# --- Routes ------------------------------------------------------------------

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        files = request.files.getlist('videos')
        saved = []

        for f in files:
            if f and allowed_file(f.filename):
                # Generate a unique file base
                base = str(uuid.uuid4())
                in_path = os.path.join(app.config['UPLOAD_FOLDER'], base + '.mp4')
                out_path = os.path.join(app.config['OUTPUT_FOLDER'], base + '.webm')

                # Save original file
                f.save(in_path)

                # Convert using ffmpeg-python
                (
                    ffmpeg
                    .input(in_path)
                    .output(
                        out_path,
                        vcodec='libvpx-vp9',
                        crf=18,
                        video_bitrate='0',    # correct way to say b:v=0
                        acodec='libopus',
                        audio_bitrate='192k',
                        threads=4,
                    )
                    .run(overwrite_output=True)
                )



                saved.append(base + '.webm')

        return redirect(url_for('gallery'))

    return render_template('index.html')


@app.route('/gallery')
def gallery():
    vids = os.listdir(app.config['OUTPUT_FOLDER'])
    vids = [v for v in vids if v.lower().endswith('.webm')]
    return render_template('gallery.html', vids=vids)


@app.route('/video/<filename>')
def video_file(filename):
    """Serve WebM files for playback in the browser."""
    return send_from_directory(
        app.config['OUTPUT_FOLDER'],
        filename,
        mimetype='video/webm'
    )


@app.route('/download/<filename>')
def download_file(filename):
    """Force-download a converted WebM file."""
    return send_from_directory(
        app.config['OUTPUT_FOLDER'],
        filename,
        mimetype='video/webm',
        as_attachment=True
    )


# --- Entry Point -------------------------------------------------------------

def main():
    """CLI entry point for 'mp4-2-webm'."""
    app.run(debug=True)


if __name__ == "__main__":
    main()
