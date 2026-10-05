import json
import io

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
video = 0.0
audio = 0.0
print(f"idx  {'text':<24s} {'vo_dur':>6s} {'scene_dur':>8s} | {'vid_start':>9s} {'aud_start':>9s} {'drift':>8s}")
for i in range(n):
    row = ct[i]
    vd = row["vo_dur"]
    sdur = sd[i]
    print(
        f"{i + 1:3d}  {row['text'][:24]:<24s} {vd:6.3f} {sdur:8.3f} | "
        f"{video:9.3f} {audio:9.3f} {audio - video:+8.3f}"
    )
    video += sdur
    audio += vd
    if i < n - 1:
        audio += gap
print(f"total video={video:.3f} audio(vo+gaps)={audio:.3f} final_dur={d.get('duration_actual_s')}")
