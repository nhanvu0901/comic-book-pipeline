# Fonts

Stage 5 captions use **Anton** (Google Fonts, SIL OFL 1.1 license, free for commercial use).

Download instructions:
  curl -L "https://fonts.google.com/download?family=Anton" -o anton.zip
  unzip anton.zip "Anton-Regular.ttf" -d .
  rm anton.zip

Or download manually from https://fonts.google.com/specimen/Anton and drop `Anton-Regular.ttf` here.

Stage 5 will look for `fonts/Anton-Regular.ttf` and fall back to a system font (Impact, Bebas Neue) if missing.
