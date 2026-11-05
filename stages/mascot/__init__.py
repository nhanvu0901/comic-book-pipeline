"""Mascot step (UI step 9): a 2-D sticker narrator (body + face kit, whole-sprite motion)
drawn onto the finished final.mp4. Runs after Stage 5; nothing upstream depends on it, and a
project without mascot.json is never touched.

Modules: config (mascot.json contract) · kit (layers + compositor) · motion (cues → transform)
· render (clean copy, still frames, preview, final) · timeline (markers, snapping, strips)
· suggest (auto cues). CLI: ``python -m stages.mascot --project <slug>``.

Design: todo/avatar-narrator/2026-11-05-mascot-step-design.md
"""
