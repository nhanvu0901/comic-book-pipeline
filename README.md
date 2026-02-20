# Comic Book Video Pipeline

A semi-automated pipeline that transforms comic book story prompts into short narrated videos with Ken Burns effects, background music, and subtitles.

## Architecture

```
YOUR MAC (Local)                    GOOGLE COLAB (GPU)
────────────────                    ──────────────────
Stage 1: Script Generator           
  (GLM-4.7 + web search)            
         │                           
         ▼                           
Stage 2: Image Picker               
  (Streamlit UI + DuckDuckGo)       
         │                           
         ├── upload to Google Drive ─→ Stage 3: TTS (Qwen3-TTS)
         │                            Stage 4: SRT Generation
         │   ←── sync back ──────────     │
         ▼                                │
Stage 5: Video Assembler ←────────────────┘
  (MoviePy + FFmpeg)                 
         │                           
         ▼                           
Stage 6: Final Output               
  1920x1080 MP4 + SRT file          
```

## Quick Start

### 1. Install Dependencies (Mac)

```bash
cd comic-video-pipeline
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Set Up Google Drive

Install [Google Drive for Desktop](https://www.google.com/drive/download/) and set your sync path in `.env`:

```
GDRIVE_BASE=~/Library/CloudStorage/GoogleDrive-youremail@gmail.com/My Drive/comic-pipeline
```

### 4. Run the Pipeline

```bash
# Stage 1: Generate script from prompt
python -m stages.stage1_script_generator "The death of Gwen Stacy in Amazing Spider-Man"

# Stage 2: Pick images (opens Streamlit UI)
streamlit run stages/stage2_image_picker.py

# Stage 3 & 4: Go to Google Colab, open colab/tts_and_srt.ipynb
# (see Colab section below)

# Stage 5: Assemble final video
python -m stages.stage5_video_assembler
```

### 5. Google Colab Setup

1. Upload `colab/tts_and_srt.ipynb` to Google Colab
2. Mount Google Drive
3. Set the project name to match your local project
4. Provide a 3-10 second voice sample WAV file
5. Run all cells
6. Audio + SRT will sync back via Google Drive

## Project Structure

```
comic-video-pipeline/
├── README.md
├── .env.example
├── requirements.txt
├── config.py                    # Shared configuration
├── stages/
│   ├── __init__.py
│   ├── stage1_script_generator.py   # GLM-4.7 script gen
│   ├── stage2_image_picker.py       # Streamlit image picker
│   └── stage5_video_assembler.py    # Video assembly
├── utils/
│   ├── __init__.py
│   ├── srt_generator.py        # Scene-level SRT from timing
│   ├── image_search.py         # DuckDuckGo image search
│   └── kenburns.py             # Ken Burns effect engine
├── colab/
│   └── tts_and_srt.ipynb       # Colab notebook for TTS + SRT
└── templates/
    └── system_prompt.txt        # LLM system prompt for script gen
```

## Configuration

All settings are in `config.py`. Key options:

| Setting | Default | Description |
|---------|---------|-------------|
| VIDEO_WIDTH | 1920 | Output video width |
| VIDEO_HEIGHT | 1080 | Output video height |
| VIDEO_FPS | 30 | Frames per second |
| MAX_DURATION | 120 | Maximum video length (seconds) |
| CROSSFADE_DURATION | 0.5 | Transition between scenes |
| BGM_VOLUME | 0.15 | Background music volume (0-1) |

## Notes

- Background music: Place your BGM file as `bgm.mp3` in the project folder
- Voice sample: Provide a 3-10 second WAV for Qwen3-TTS voice cloning
- Images are resized/cropped to 1920x1080 automatically
- SRT is generated at scene-level by default; Whisper upgrade available in Colab
