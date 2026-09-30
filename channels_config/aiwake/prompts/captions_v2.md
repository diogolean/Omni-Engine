# Aiwake captions v2

Write platform-native US English for one AI-vs-AI debate. Input is that video's spoken turns only.

Return strict JSON with keys: tiktok, instagram, facebook, youtube_title, youtube, x, linkedin, pinterest_title, pinterest, kwai.

Rules:
- Line 1 is a hook specific to this exchange. No bracket tags. No "X vs Y" as line 1.
- Quote the answerer's words verbatim, trimmed only at a sentence boundary.
- One viewer question per caption. It must be a full sentence. Never cut a sentence and add "?".
- Do not say frontier, zero human script, unscripted, caught, collapsed, forced to retreat, or a fixed render time.
- Do not reuse a sentence or a hashtag set from the avoided list.
- Model names only if they spoke. Never "X vs X".
- Disclose that the clip is AI-animated without pasting the same sentence onto every video.
- If the answerer never spoke, return {"blocked": true}.
