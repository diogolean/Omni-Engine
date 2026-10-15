# Aiwake captions v4

You pick one punchline from this clip. You do not write the title, the description, or the hashtags. Code writes those.

Return one JSON object:

- quote: a verbatim punchline from the utterances, 4 to 20 words, with no added words.
- quote_speaker: the speaker of that quote, using Gemini, Llama, GPT-4o, DeepSeek, or Claude.
- verdict_evidence: empty, unless you would describe the clip with admit, corner, confess, collapse, dodge, or caught. Then this must be a verbatim transcript line.

Code sets the title to `<finished question>? | <Asker> vs <Answerer>`.
Code sets every description to a clash line, your quote, one viewer question, the fixed unscripted disclosure, and exactly 4 hashtags.
