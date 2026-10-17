# Diagrams

## Content flow

```mermaid
flowchart TD
    script[Voiceover script] --> tts[TTS plus word timestamps]
    tts --> beats[Beat splitter 4 to 6 seconds]
    beats --> stills[One image per beat]
    stills --> gate[Spoken-line vision gate]
    gate -->|pass| compile[Compile MP4]
    gate -->|fail or judge down| block[Block publish and log]
    compile --> mp4[MP4 gate: frames, audio, duration]
    mp4 -->|verdict pass| report[QA report JSON]
    mp4 -->|reject| block
    report --> planner[Post Planner xlsx]
    report --> youtube[YouTube private plus publishAt]
    planner --> facebook[WordPress bot to Facebook]
    report --> b2[Backblaze B2 object]
    b2 --> pinterest[VM Pinterest drip]
```

## Deployment

```mermaid
flowchart LR
    subgraph gh [GitHub]
        push[push main]
        actions[Actions build arm64]
        ghcr[GHCR image sha]
    end
    subgraph vm [Oracle VM]
        engine[Docker engine]
        scheduler[Docker scheduler profile]
    end
    subgraph pc [PC runner]
        aiwake[Aiwake animated]
        mei[Master Mei batches]
    end
    subgraph store [Backblaze B2]
        media[media objects]
        ledger[library and publish ledger]
    end
    wp[WordPress bot]
    fb[Facebook]
    pin[Pinterest]
    yt[YouTube]

    push --> actions --> ghcr --> engine
    ghcr -.-> scheduler
    engine --> media
    pc --> media
    media --> ledger
    engine --> yt
    scheduler --> pin
    engine --> wp
    wp --> fb
    media --> pin
```

Deploy starts `engine` only. The dashed line is the scheduler image, which stays stopped until `SCHEDULER_ENABLED=1` or an operator starts the Compose profile.

## Pinterest publish sequence

```mermaid
sequenceDiagram
    participant sch as VM scheduler
    participant b2 as Backblaze B2
    participant led as publish ledger
    participant wp as WordPress
    participant pin as Pinterest API

    sch->>b2: list image keys under the account prefix
    b2-->>sch: keys only, no bodies
    sch->>led: drop keys already recorded for this account and board
    sch->>wp: fetch the article catalog
    wp-->>sch: slug, title, body
    sch->>sch: match one filename to one article
    sch->>b2: download that single object
    sch->>sch: cover-crop a 2:3 card
    sch->>pin: create the pin
    pin-->>sch: pin id
    sch->>led: record account, B2 object, article, pin id
    sch->>sch: delete the temp file
```
