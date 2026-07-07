from dataclasses import dataclass


@dataclass
class Config:
    """All tunable thresholds for the highlight pipeline."""

    # Stage 1 — detection
    min_speed_kmh: float = 3.0          # below this = standstill, dropped
    score_cutoff: float = 0.35          # per-second interest score threshold [0,1]
    gps_weight: float = 0.6             # weight of GPS speed in interest score
    flow_weight: float = 0.4            # weight of optical flow in interest score
    speed_reference_kmh: float = 35.0   # speed mapped to 1.0 (absolute scaling)
    flow_reference: float = 3.0         # flow magnitude mapped to 1.0
    flow_sample_fps: float = 2.0        # frames sampled per second for optical flow
    min_segment_seconds: float = 2.0    # discard segments shorter than this
    gap_bridge_seconds: float = 2.0     # merge kept segments separated by <= this
    sparkline_columns: int = 100        # width of --inspect ASCII sparklines

    # Stage 2 — edit / audio
    original_audio_volume: float = 0.4  # multiplier on original video audio
    music_volume: float = 1.0           # multiplier on music track
    fade_out_seconds: float = 3.0       # music fade-out at end
    cut_mode: str = "reencode"          # "reencode" (frame-accurate) or "copy"

    # Stage 3 — intro
    intro_duration: float = 7.0         # seconds

    # Segment overlay (lower-third)
    overlay_font_path: str = "/System/Library/Fonts/Supplemental/Arial.ttf"
    overlay_band_opacity: float = 0.5
    overlay_name_fontsize: int = 48
    overlay_stats_fontsize: int = 32

    # HUD (segment-mode telemetry overlay)
    hud_enabled: bool = True
    hud_fps: int = 15
    speedo_max_kmh: float = 45.0

    # Output resolution
    output_height: int = 1080   # final video height; width follows source aspect
