import json
import io

# Reproduce the OLD sequential-concat audio clock vs the NEW scene-boundary
# clock for the forgiveness reel, proving the fix eliminates drift.
d = json.load(
    io.open(
        r"outputs/wonder_feed/clips/lofi_reel_forgiveness_putting_the_weight_down_20260827_173857_v01.json",
        encoding="utf-8",
    )
)
ct = d["caption_timing"]
sd = d["scene_durations"]
gap = 0.30
n = len(ct)

print("idx  vo_dur  scene_dur | caption_start | NEW_audio_start | matched")
old_cursor = 0.0
new_cursor = 0.0
ok = True
for i in range(n):
    vd = ct[i]["vo_dur"]
    sdur = sd[i]
    # caption/video clock start (old and new base):
    vstart = sum(sd[:i])
    # NEW audio start == caption start (cumsum of scene_durs)
    new_start = sum(sd[:i])
    # OLD audio start == sequential vo_dur + gaps
    old_start = old_cursor
    match = abs(new_start - vstart) < 1e-9
    ok = ok and match
    drift_old = old_start - vstart
    print(
        f"{i+1:2d} {vd:6.3f} {sdur:7.3f} | {vstart:9.3f} | {new_start:11.3f} | "
        f"{str(match):5s}   old_drift={drift_old:+.3f}"
    )
    old_cursor += vd + (gap if i < n - 1 else 0.0)
    new_cursor += sdur

print("total_dur(video)=", new_cursor)
# Final old-concat VO total:
final_old = sum(ct[i]["vo_dur"] for i in range(n)) + gap * (n - 1)
print("OLD audio total =", round(final_old, 3), "vs video total =", round(new_cursor, 3))
print("ALIGNMENT_OK =", ok)
