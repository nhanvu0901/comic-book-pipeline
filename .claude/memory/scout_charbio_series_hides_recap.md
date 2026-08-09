2026-07-17: "Edge of Spider-Verse #5 (Peni Parker)" was marked verified-clean in the CSV because
the only hit was "WHO IS PENI PARKER? SP//dr's Story Begins in Edge of Spider-Verse #5 (Part
1/3)" — logged as "long-form multi-part series, NOT a Short" and treated as non-disqualifying.
That reasoning was WRONG: pulling the actual video description (via yt-dlp-free method:
`curl -A "Mozilla/5.0" <watch-url> | grep -o '"shortDescription":"[^"]*"'`) showed it explicitly
recaps the issue's plot beat-by-beat ("Witness her rise as a hero, her battle against Mysterio,
and her unexpected team-up with Daredevil... this exciting recap") — a full English narration of
THIS exact issue, just packaged as part 1 of a 3-part character-bio series (channel: Comic
District). Lesson: a "WHO IS X?" / "Entire History of X" bio-series hit is NOT automatically
exempt from HAS_NARRATION — pull the description/length (oembed gives title+channel only, use
the shortDescription grep against the raw watch page) and check whether "Part 1" = a full recap
of the exact target issue before calling it clean.

Also confirmed this run: pulling `"shortDescription"` off the raw YouTube watch HTML (curl with
a UA string, since WebFetch on youtube.com returns only footer nav) is a cheap way to tell
narration-recap vs review/reaction without opening the video — check for phrases like "recap",
"full story", or a plot-beat list.
