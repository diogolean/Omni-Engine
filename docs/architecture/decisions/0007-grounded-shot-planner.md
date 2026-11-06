# 0007 — Grounded shot planner

## Status

Proposed. This record is the design. It does not change the pipeline.

The implementation lands as the small pull requests in [Phased plan](#phased-plan). Until `REEL_SHOT_PLANNER` is on for a channel, today's prompt path keeps running, including the Ancient Knowledge grounding on [PR #14](https://github.com/diogolean/Omni-Engine/pull/14).

## Context

Ancient Knowledge can produce a cinematic reel and the next run of the same topic can miss. London Hammer once scored a first-pass hit rate of 0.84. Lost OOPArts then scored 0.50, and the same topic on the next run scored 0.32. Strict reel QA (ADR 0005) stayed at 0.70 and correctly refused to publish the weak runs. The pictures were the problem. The threshold is right.

The misses come from how a still is chosen, not from one bad prompt line.

- A subject pool always offers "Wide Landscape / Ruins" and "Monument from an unusual vantage", so a vague beat becomes ruins.
- One style block (`ATMOSPHERE_STYLE`, `ILLUSTRATION_STYLE`, and the photoreal body in the Ancient Knowledge adapter) asks every frame for megaliths, amber torchlight, alien glyphs, and sometimes a hovering craft.
- One domain lock is chosen for the whole script. A single Antikythera mention anchors every later beat to "marble architecture, clockwork gears", and Flux draws a clock face or a vault door.
- Global keyword bans then try to undo the addiction. `pyramid` and `egypt` were banned because the model defaults to Giza. A Great Pyramid episode, a Sphinx episode, and the Serapeum boxes are exactly the cases where those words are the picture. A doorway ban was added because doorway framing dominated every reel. The Serapeum is an underground stone gallery, and a narrated temple corridor is a real shot.
- PR #14 patches this per artefact: a regex picks the object, a hardcoded look describes it, and an allow-list drops Egypt bans on the Serapeum only. That held the last offline fixture. It will need another allow-list the next time a new subject conflicts with a ban.

The same shape has to serve Wonder Feed, Master Mei, and later aiwake. Those channels do not share Ancient Knowledge's landmark list. They do share the need for a shot that matches the sentence.

Super Memory (ADR 0006, the arm64 service) stays optional. It is not the store for this planner. Channel RAG already has a JSON file as the source of truth and Chroma as an optional mirror (`VISUALQA_USE_CHROMA=0` forces JSON). That JSON store, plus a small entity file, is the grounding context.

## Decision

A shared module, `modules/shot_planner/`, turns a topic or an optional source into a schema-checked shot list. Image prompts are rendered from that list with no further model call. Each channel supplies a short config and an entity knowledge base. Ancient Knowledge is the first channel. The others reuse the module by adding their own files.

```text
topic or source
    │
    ├─ channel RAG (JSON, Chroma optional)
    ├─ entity KB for the entities this topic names
    └─ recent subjects from visual_memory.jsonl
            │
            ▼
      one structured LLM call
            │
            ▼
      schema check ──fail──► one repair call ──fail──► stop, no images
            │
            ▼
      KB canonical visual wins for a known entity_id
            │
            ▼
      deterministic prompt + per-shot negatives
            │
            ▼
      existing Flux + QA at 0.70 (ADR 0005)
```

The script stage emits the shot list. Narration in the list is what TTS speaks. Each shot is one spoken sentence, or a short sentence group that fits the still duration, so a later audio split cannot start a beat on a leftover word such as "perfectly."

`REEL_SHOT_PLANNER=1` enables the path for channels that have a config file. Unset or `0` leaves the current prompt path in place. The first implementation defaults the flag off.

### What the module owns

| Piece | Role |
|---|---|
| `schema.py` | Required fields and a stdlib validator. No pydantic, no `jsonschema` package. |
| `entities.py` | Load `channels_config/<id>/entities.json`. Match aliases. |
| `plan.py` | Build the planner prompt, call the model once, validate, repair once. |
| `render.py` | Build the positive prompt and the negative prompt from one shot. |
| `diversity.py` | Recency counts from `outputs/<channel>/_review/visual_memory.jsonl`. |
| `source.py` | Optional URL or pasted text, size-capped, no browser. |

Per channel:

- `channels_config/<id>/shot_planner.json` — hard negatives, recency tags, shot-type vocabulary, voice reminders.
- `channels_config/<id>/entities.json` — artefacts, places, and phenomena with a canonical picture.

The module does not import `main.py`, `caption_engine`, or `core/reel_sequence_engine.py`. Those imports pull NumPy and are too heavy for the CI image and for a unit test.

### Grounding rules

1. A known `entity_id` keeps the knowledge-base `canonical_visual`. The model's paraphrase does not replace it.
2. An unknown entity is kept, marked `kb_miss: true`, and uses the model's visual description. The eval counts those misses. The run still proceeds.
3. The positive prompt leads with the canonical visual, then shot type, camera, setting, era, mood, and lighting. Channel atmosphere is a default the shot overrides. Torchlight appears when this shot's lighting asks for it.
4. Negatives are the channel hard list, plus this entity's `confusions`, plus this shot's `negatives`. A token that names this shot's own subject is removed. A Great Pyramid shot does not carry `pyramid` as a negative.
5. Other entities in the same episode stay out of this frame by appearing in the negative prompt. They are not written into the positive prompt. PR #14 showed that "do not depict a cannon" inside the positive prompt still draws a cannon, and that a 400-character judge window was cutting the prompt off mid-sentence.
6. The vision judge receives the shot's subject label and canonical visual as their own fields. It does not have to recover the expected object from a truncated prompt.
7. A subject the narration names is never penalized away. Consecutive beats that explain the same object stay on that object. Diversity applies to subjects the narration did not ask for, and to compositions that keep returning (doorway, torchlight, hovering craft, wide ruins) when this shot's setting is something else.

### Diversity and recency

`visual_memory.jsonl` (ADR 0006, the grounding record) already stores judged stills and, on Ancient Knowledge, the angle of an accepted script. The planner reads the last eight episodes.

For each recency tag and each entity id, count how often it led a still. A tag or entity that led more than half of those episodes is listed in the planner prompt as recently used. The model may use it when this beat names it. Otherwise the renderer adds it to the negative prompt.

Inside one episode the cap is one use of a recency tag unless the shot's setting or narration is that thing. A Serapeum gallery may be a stone chamber. The next episode does not open on another doorway unless its script asks for one.

Angle memory stays. The planner receives the last six angles for this topic slug and writes a new angle. Revisiting Lost OOPArts is welcome. Repeating the same hook and the same hero object is the case the penalty handles.

### Source-driven and from-scratch

The same call accepts either a topic or an external source.

- Pasted text is sent with the channel voice rules.
- A URL is fetched with the stdlib, HTTP or HTTPS only, a short timeout, and a cap of about 32 KB of extracted text. Tags are stripped with `html.parser`. There is no browser and no extra dependency.
- A failed fetch stops the run and reports the error. The pipeline does not invent the article.
- From-scratch is the same schema with `source.kind = "topic"`.

Narration follows the channel `script_rules` already stored for Ancient Knowledge: a calm investigative voice, a named site or artefact in the opening, a date or measurement when the source supports it, theories hedged, and the existing forbidden phrases left unused ("welcome back", "you won't believe", and the rest of that list). The model does not add a measurement, a date, or a quote the source does not contain.

The spoken text is prose. The shot list is the camera plan for that prose.

## Data schemas

### Shot list

One object per episode. Field names are stable so Wonder Feed and Master Mei can fill the same structure.

```json
{
  "channel": "ancient_knowledge",
  "topic": "Lost OOPArts",
  "angle": "museum storage, not the discovery myth",
  "source": {"kind": "topic", "ref": "", "title": ""},
  "narration": "Full spoken script.",
  "shots": [
    {
      "id": "s01",
      "narration": "The Antikythera Mechanism came out of a shipwreck.",
      "entity_id": "antikythera_mechanism",
      "subject": "Antikythera Mechanism",
      "canonical_visual": "corroded bronze gear fragments in a museum case",
      "shot_type": "macro",
      "camera": "close three-quarter, subject fills the frame",
      "setting": "museum conservation drawer",
      "era": "Hellenistic, recovered 1901",
      "mood": "quiet, investigative",
      "lighting": "single raking museum lamp",
      "negatives": ["clock face", "vault door", "pyramid", "hovering craft"],
      "kb_miss": false
    }
  ]
}
```

Required on every shot: `id`, `narration`, `entity_id`, `subject`, `canonical_visual`, `shot_type`, `camera`, `setting`, `era`, `mood`, `lighting`, `negatives`. `shot_type` is one of the channel's allowed types (`macro`, `close`, `medium`, `wide`, `aerial`, `over_shoulder` for Ancient Knowledge). `negatives` is a list of short phrases. Empty is valid when the entity's confusions already cover the risk.

`source.kind` is `topic`, `url`, or `text`.

### Entity knowledge base

A JSON array. Small and curated. Ancient Knowledge starts with the golden-set subjects below, on the order of a few dozen entries, not a crawl of every site.

```json
{
  "id": "antikythera_mechanism",
  "aliases": ["antikythera", "antikythera mechanism"],
  "kind": "artifact",
  "canonical_visual": "corroded bronze gear fragments in a museum case",
  "era": "Hellenistic Greece, about 100 BC",
  "setting": "museum case",
  "confusions": ["clock face", "vault door", "wristwatch", "clock tower"],
  "channels": ["ancient_knowledge"]
}
```

`kind` is `artifact`, `place`, or `phenomenon`. `confusions` are the pictures this subject gets mistaken for. They are negatives for this entity only.

Seed looks, taken from the subjects PR #14 already had to describe by hand, plus the places the golden set needs:

| id | canonical visual |
|---|---|
| `antikythera_mechanism` | corroded bronze gear fragments in a museum case |
| `coso_artifact` | a small manufactured piece sealed inside a split rock geode |
| `klerksdorp_spheres` | small grooved metal spheres |
| `serapeum_boxes` | massive rough granite boxes in an underground stone gallery |
| `quimbaya_ornaments` | small golden ornaments shaped like ancient jet aircraft |
| `london_hammer` | an iron hammer head and wooden handle encased in stone at London, Texas |
| `great_pyramid` | the limestone mass of the Great Pyramid at Giza |
| `great_sphinx` | the limestone Sphinx and its enclosure walls |
| `nazca_lines` | geoglyphs on the pale Nazca desert, seen from above |
| `gobekli_tepe` | T-shaped limestone pillars in a buried circular enclosure |
| `saqqara_bird` | a small wooden bird with a vertical tail |
| `dendera_relief` | a carved wall relief at Dendera, the motif cut into stone |

London, Texas is a find-spot. The prompt names the town in Texas. The famous city is a confusion on that entity, the same rule PR #14 encoded as a place warning.

A geology beat about the Coso artifact keeps the same entity and changes `canonical_visual` for that shot only, to a rock concretion or a geode cross-section. The knowledge-base line stays the manufactured piece. The shot records which reading the narration is using.

### Channel config

```json
{
  "channel": "ancient_knowledge",
  "enabled_flag": "REEL_SHOT_PLANNER",
  "shot_types": ["macro", "close", "medium", "wide", "aerial", "over_shoulder"],
  "hard_negatives": [
    "text", "watermark", "logo", "caption", "letterboxing",
    "illustration", "sketch", "cartoon", "CGI", "3D render",
    "flat lighting", "smartphone", "modern clothing"
  ],
  "identity_negatives": [
    "relationship", "attachment", "therapy", "couple", "graphite", "wellness"
  ],
  "recency_tags": [
    "pyramid", "sphinx", "doorway", "archway", "torchlight",
    "hovering craft", "wide ruins"
  ],
  "recency_window_episodes": 8,
  "max_tag_uses_per_episode": 1
}
```

`recency_tags` are not bans. `max_tag_uses_per_episode` yields when the shot's narration or setting names the tag.

## Audit: Ancient Knowledge bans and RAG rules

Read from `quality/VisualQA_Agent/channel_rag.py` (`CHANNEL_DNA_SEED["ancient_knowledge"]`), the mirror in `channel_dna_store.json`, `agents/media/scene_prompt_generator.py`, `agents/media/prompt_alignment.py`, and `channels_config/ancient_knowledge/`.

### Workarounds that become a diversity penalty

These exist because a picture got stuck. They also block the picture when the script asks for it.

| Where | Tokens or rule | What it was papering over | Replacement |
|---|---|---|---|
| `_EGYPT_SLOP` in `scene_prompt_generator.py` | pyramid, pyramids, egyptian, egypt, pharaoh, pharaohs, sphinx, giza | Flux defaults to Giza on any "ancient" prompt. The ban is skipped only when the topic or the spoken window contains an Egypt hint, so one mixed episode still treats Egypt as illegal on the other beats. PR #14 adds a per-object allow-list for the Serapeum only. | `pyramid` and `sphinx` are recency tags. A beat whose entity is the Great Pyramid or the Sphinx shows them. A Lost OOPArts beat leaves them in that shot's negatives because the shot list did not name them. |
| RAG `forbidden_tokens`, the composition block | archway, doorway, portal, window frame, silhouette frame, stone chamber interior, framed by stone, framed through, gateway, ceremonial gate, temple corridor | Doorway framing was dominating every reel. The comment in the seed says so. | Recency tag, at most one per episode unless the setting is that space. |
| Same list, copied | `channel_dna_store.json` lines for those tokens; `prompt_alignment._DEFAULT_DOORWAY_WORDS`; `plan_episode_visual_sequence(..., max_doorway_uses=1)`; adapter `_DOORWAY_BAN` ("ABSOLUTELY NO archways…") on every `compose_image_prompt` | Three copies of the same cap, so a gallery cannot get through. | One recency rule in `shot_planner.json`. The three copies come out of the planner path. |
| `_SUBJECT_POOL` entries marked always relevant | "Wide Landscape Ruins", "Monument Unusual Vantage" | Every episode is offered ruins, including a museum drawer and a lab bench. | Shot type comes from the shot. "Wide ruins" is a recency tag. |
| `ATMOSPHERE_STYLE`, `ILLUSTRATION_STYLE`, `_PHOTOREAL_BODY` | megaliths, rim-lit stone, amber torchlight, alien glyphs, hovering craft, on every still | Channel mood became the subject. | Default mood, overridable per shot. Torchlight and hovering craft are recency tags. |
| `_CAMERA_OPENERS` | one opener is "a disc-shaped craft hovering above the ruins", chosen at random | A craft appears on topics that never mention one. | The camera line is the shot's `camera` field. |
| `_DOMAIN_PROFILES["hellenistic_greece"].anchors` | "marble architecture, bronze patina, clockwork gears" applied to the whole script | One Antikythera sentence pulls every later object into clockwork. Clockwork in the positive prompt primed clock faces. | Per-shot `setting` and `era`. Clock face and vault door are `confusions` on the Antikythera entity. |
| PR #14 `_DRIFT` | cannon, artillery, firearm, cyberpunk character, classical statue, foggy monument, torchlit ruins, clock face, vault door, fantasy landscape, city street, on every beat | Those words were leaking into positives and the model drew them. A global negative list is the same kind of workaround as the Egypt list. | Each phrase lives on the entity that attracts it (Coso theory beats: cannon and statue; Antikythera: clock face and vault door). A beat that does not have that entity does not need the whole list. |
| RAG `mandatory_elements` | "Atmospheric cinematic still of ancient ruins…"; "Fantasy or surreal ancient-mystery interpretations are valid" | The critic is told that ruins are the default success. | The shot's canonical visual is the success condition. The sentence that already says a named monument is required only when the prompt asked for one stays. It matches this design. |
| RAG `visual_concepts.pyramid` | Great Pyramid / Sphinx text bundled into image guidance whenever concepts are injected | Useful as knowledge. Harmful when it is pasted into a Nazca or Antikythera prompt. | The planner receives the entity entries that match this topic, not the whole concept map. |
| RAG `motion_rules.parallax_depth_directive` | every prompt must include a "stone arch fragment" and a "temple silhouette" | Foreground and background are hard-coded as architecture. | Foreground and background come from the shot's setting. Motion cadence itself stays; it is timing, not a subject. |
| RAG `motion_rules.post_fx_toggles` | torch flicker and warm-gold light rays on every Ancient Knowledge reel | A museum lamp shot still gets firelight in post. | Post effects follow the shot's lighting. A later phase can wire that. Phase 3 only stops the prompt from requesting torchlight on every beat. |

### Rules that stay hard negatives

These are the medium, the channel boundary, or a safety rule. A diversity penalty would eventually let them back in. That is the wrong outcome.

| Where | What stays | Why it stays |
|---|---|---|
| RAG `forbidden_tokens`, the medium block | CGI, 3D render, Unreal Engine, illustration, sketch, cartoon, graphite, front-facing postcard, flat lighting, studio softbox, modern clothing, smartphone, lifestyle photography | Ancient Knowledge is a photoreal documentary frame. These are the wrong medium, not an over-used subject. |
| `page_config.PROMPT_NEGATIVE_TERMS` and adapter `_FORBIDDEN_CROSS_CHANNEL` | relationship, attachment, therapy, couple, graphite, Anna, microbiome, wellness, and the rest of that identity list | Isolation from Wonder Feed, Anna Protocol, and Master Mei. |
| Adapter `_OFF_NICHE_NEEDLES` | the same boundary, applied to topic selection | Topic gate, not an image workaround. |
| RAG `script_rules.forbidden_phrases` | "welcome back", "you won't believe", "ancient civilizations were more advanced than we thought", and the other lines in that list | Narration voice. The planner keeps them as script rules. |
| RAG `script_rules` voice, hook, pacing, CTA | investigative voice, named site in the opening, duration-based word budget, CTA stitched after the narration | The planner writes narration inside these rules. |
| RAG `target_audience_rules`, last sentence | a named monument is required only when the prompt asked for one | This is already the right critic instruction. |
| Master Mei `forbidden_tokens` | firearms, gore, gym, Gossip Goblin | Safety and brand for that channel. They move into Master Mei's `hard_negatives` when that channel adopts the module. They are not an Ancient Knowledge recency tag. |
| LOFI economic `forbidden_tokens` | photoreal, selfie, NSFW, baked-in text | That channel's medium is illustration. The hard list is the inverse of Ancient Knowledge's, which is why it has to be per-channel config. |
| ADR 0005 | relevance threshold 0.70, three attempts, fail closed | Unchanged. A weak still does not ship. |

`agents/rag/channel_rag.py` `get_image_guidance` currently injects the whole `forbidden_tokens` list into the image prompt. On the planner path that injection stops. Hard negatives come from `shot_planner.json`. The composition tokens in the same list are not passed through as bans.

## Migration from PR #14

PR #14 (`cursor/reel-image-grounding-b2fd`) is the bridge. It should merge, or stay the base of the implementation PRs, for the pieces that are already the right mechanism.

Keep, and do not re-implement:

- QA at 0.70, fail closed, with a technical judge failure retried and a real mismatch rejected (ADR 0005).
- `visual_memory.jsonl` via `review_path`, including `kind: "angle"` rows and `REEL_ANGLE_MEMORY`.
- Spoken sidecar cache: a still is reused only when `.spoken.json` records a pass for the same line.
- Portable MP4 probe (PATH ffmpeg, then the imageio-ffmpeg binary) and the numeric timing filter that keeps years and quantities in the captions.
- Sentence-boundary splitting in the audio chunker, so a beat does not open on a fragment.
- Public-domain stills off unless `REEL_FREE_IMAGES` is set.

Retire once `REEL_SHOT_PLANNER` is on for Ancient Knowledge:

- `resolve_beat_object` and the proper-name regex as the way a beat finds its subject. The shot list already names `entity_id`.
- The hardcoded `_KNOWN` looks in `modules/reel_grounding/brief.py`. Those strings are the seed of `entities.json`.
- `allow_bans` on a single object. That is the Egypt-ban exception this design deletes.
- `_EGYPT_SLOP` as a control on the planner path.
- `_DRIFT` pasted onto every beat.
- The subject-pool draft that `main.py` builds and then throws away when grounding succeeds. The renderer is the only prompt.

While the flag is off, `assemble_beat` keeps today's behavior so a rollback is a config change. While the flag is on, `assemble_beat` renders the shot and does not call the regex.

The offline report `python -m modules.reel_grounding.report` stays useful during the migration. The planner adds a sibling check that prints the shot list without generating images. Phase 5 replaces both as the harness.

Tests that currently require `pyramid` inside the negative prompt of an Antikythera beat (`tests/test_audio_first_pipeline.py`) change in Phase 3. Until that phase they stay, because the ban is still the live behavior.

## Cost per video

The cost tracker already prices FLUX.1-schnell at $0.003 per generated image and Gemini tokens at $0.075 per million (`main.py`, the cost-block comment). TTS stays at $0.00003 per character. Those are the rates used below.

A default Ancient Knowledge reel is 80–90 seconds, about 2.25 words per second, and about 18 stills at the fast-cut tier.

| Line | What happens | Cost |
|---|---|---|
| Planner | One structured call. A few thousand tokens in (voice rules, the matching entity cards, the recent-subject list, and the source if any) and a few thousand out (narration plus shots). | Under $0.01. A 6,000-token call at $0.075 per million is a fraction of a cent. A long article plus one repair call stays under about $0.02. |
| Repair | A second call only when the JSON fails schema. | Same band, and only on a bad reply. |
| Images, first pass clean | 18 × $0.003 | about $0.054 |
| Images, at a 75% first pass | The other quarter of the beats redraw once. | about $0.07 |
| Images, worst case inside the QA cap | Three attempts on every beat. | about $0.16, which is the current failure mode when the hit rate sits near 0.32 and the run still does not ship. |
| TTS | Unchanged. A 90-second script is a few cents. | existing |
| Vision judge | Unchanged. The repo does not publish a per-call price for the Qwen-then-Gemini chain. | existing |
| URL fetch | HTTP only, no model. The article text is part of the planner input. | included in the planner line |
| Super Memory, Chroma | Not on this path. | $0 |

So a reel that hits the 70–80% band costs about **$0.07 in images plus under $0.01 for the planner**, on top of the TTS and judge spend the pipeline already pays. The planner is one extra language-model call per video, two if the schema fails.

A full paid eval (the optional mode below) is about $2: eight topics, three runs, about $0.07 of images each, plus the planner calls.

## Local and cloud constraints

- Windows local runs and the Oracle ARM Docker image both execute the module. Dependencies stay inside `requirements.txt` / `requirements-docker.txt`. The planner adds none.
- CI (`requirements-ci.txt`) can import the module. Tests use fixture JSON. They do not call a model, fetch a URL, or generate an image.
- Channel RAG on the hot path is the JSON store. Chroma remains optional.
- Super Memory remains the compose `memory` profile from ADR 0006. The planner does not read or write it. Recency uses `visual_memory.jsonl` under `review_path`.
- URL fetch is size-capped and fail-closed. No headless browser.
- Scratch files, if a phase needs them, go through `temp_path` and `review_path`. Nothing new at the repository root.
- The entity file and the channel config are small enough to ship inside the image.

## Eval harness

Consistency is the mean and the spread across runs, not one lucky log line.

### Golden set

Eight Ancient Knowledge topics. Pyramid episodes are in the set on purpose.

| # | Topic | What a pass looks like |
|---|---|---|
| 1 | Lost OOPArts (Antikythera, Coso, Serapeum boxes, Quimbaya ornaments) | Each segment shows its own object. A pyramid does not appear. The Serapeum may be a stone gallery. |
| 2 | Great Pyramid, chambers found by muon scan | The pyramid is the subject. |
| 3 | Sphinx enclosure erosion | The Sphinx and the enclosure are the subject. |
| 4 | London Hammer, London, Texas | The encased hammer. The picture is the Texas find-spot, not the city of London. |
| 5 | Antikythera Mechanism | Gear fragments in a museum case. A clock face and a vault door stay in the negatives. |
| 6 | Nazca Lines | Geoglyphs on the pampa. No Giza. |
| 7 | Göbekli Tepe | Buried T-shaped pillars. A pyramid is not a stand-in. |
| 8 | Saqqara Bird, or the Dendera relief | The bird or the carved relief. The Great Pyramid stays out unless a beat names it. |

### Offline mode

No image spend. This is the CI job.

- Every golden shot list validates against the schema.
- Every shot has an entity id, a subject, and a canonical visual. `kb_miss` is allowed and reported. The curated eight should have zero misses once the seed file lands.
- The rendered positive prompt starts with the canonical visual and names the subject.
- Each entity's `confusions` are absent from the positive prompt and present in the negative prompt.
- A token in `hard_negatives` is absent from the positive prompt.
- A recency tag that this shot does not name is absent from the positive prompt.
- Across the eight fixtures, `pyramid` leads the shots of topics 2 and 3 and does not lead topics 1, 5, 6, or 7.
- Doorway, torchlight, and hovering craft appear at most once per fixture unless that shot's setting or narration names them.
- Narration alignment: the subject alias occurs in the spoken line, or the line is an explicit continuation of the previous shot's entity (a theory sentence). A continuation shot keeps the previous `entity_id`.
- A frozen set of bad shot lists (missing visual, pyramid on a Nazca shot, clock face in an Antikythera positive) fails the check.

Offline fixtures are checked in. A live planner call is a separate switch, off in CI, so a network or a key is not required for a green build.

### Paid mode

`python -m modules.shot_planner.eval --paid --runs 3`

For each golden topic, generate the stills through the existing gate, N times. Print, per topic and overall:

- `first_pass_hit_rate` mean
- variance and the min run

The target is a mean in the **0.70–0.80** band and a spread that stays tight: standard deviation at or below 0.10, and no run under 0.60, once the mean is inside the band. QA remains 0.70. Paid mode is not a CI job. It is the check Diogo runs before the flag becomes the default.

The existing log line stays:

```text
[VisualQA] first_pass_hit_rate=0.78 (14/18)
```

The harness aggregates those lines. It does not loosen a single run to make the mean look better.

## Phased plan

Each phase is its own small pull request. Later phases wait until the earlier one is green.

| Phase | PR contains | Flag |
|---|---|---|
| 0 | This document. | none |
| 1 | `modules/shot_planner` schema, entity loader, deterministic renderer, Ancient Knowledge `entities.json` and `shot_planner.json`, offline unit tests on frozen shot lists. No call from `main.py`. | off, unused |
| 2 | One planner call and one repair call. Ancient Knowledge only. When the flag is on, the script stage writes the shot list and the image prompt is the render. When it is off, PR #14's resolver runs. | `REEL_SHOT_PLANNER`, default off |
| 3 | On the planner path, stop applying `_EGYPT_SLOP`, the doorway copies, and the always-on ruins pool. Recency reads `visual_memory.jsonl`. Update the Antikythera test that currently demands `pyramid` in every negative. | still default off until a paid eval is in band |
| 4 | Optional `--source-url` and `--source-text`. Failed fetch stops the run. | same flag |
| 5 | Golden set and the offline harness in CI. Paid mode behind `--paid`. | harness only |
| 6 | Wonder Feed `shot_planner.json`, with that channel's own hard negatives (it is lifestyle photography, so Ancient Knowledge's "no lifestyle" list is not copied). Proves a second channel is config. | per channel |

Master Mei is the pull request after Phase 6, not part of it. The identity lock becomes a canonical visual of Master Mei (white topknot, brow, robe, as the RAG already describes). Firearms, gore, and the gym stay in `hard_negatives`. Aiwake can adopt the module the same way later. Its Super Memory service stays optional and is not required for a shot list.

Phase 3 is the one that deletes the workaround. It lands after the renderer and the entity file exist, so a pyramid episode has a place to put a pyramid before the ban disappears.

## Consequences

- A good reel is a shot list that validates, renders, and clears 0.70, repeated across the golden set. A single London Hammer log stops being the evidence.
- Image spend per shipped reel should fall toward the $0.07 band because fewer beats burn all three attempts. A topic that still misses stays unpublished.
- The entity file is editorial. A new artefact needs a canonical visual and its confusions before it will hit the knowledge-base path. Until then it is a `kb_miss` and the eval shows it.
- Prompt text becomes boring on purpose. The variety lives in `shot_type`, `camera`, `lighting`, and the angle, which is what a documentary crew varies.
- Two ADRs already share the number 0006 (reel grounding, and central Supermemory). This record is 0007 so the sequence stays unique from here.
