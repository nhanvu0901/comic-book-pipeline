"""python -m stages.mascot --project <slug> [--suggest] [--preview T] [--frame T] [--off]

Default: render final.mp4 from projects/<slug>/mascot.json (channel defaults when absent).
  --suggest     replace unlocked cues with auto suggestions, save mascot.json, then render
  --preview T   only render mascot/preview.mp4 around T seconds
  --frame T     only write the still frame at T and print its path
  --off         set enabled=false and restore the clean final.mp4
"""
raise NotImplementedError
