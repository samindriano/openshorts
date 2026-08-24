import time
import cv2
import subprocess
import argparse
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector
from ultralytics import YOLO
import torch
import os
import numpy as np
from tqdm import tqdm
import yt_dlp
import mediapipe as mp
# import whisper (replaced by faster_whisper inside function)
from google import genai
from google.genai import types as genai_types

import gemini_worker
import gemini_rate_limiter
import layout_picker
import clip_ai
from ai_provider import normalize_provider
from clip_selection import (build_transcript_windows, clip_count_targets,
                            clip_duration_bounds, snap_clip_to_words)
from ffmpeg_utils import (video_encode_args, audio_encode_args, QUALITY,
                          QUALITY_FAST, METADATA_SCRUB)
from dotenv import load_dotenv
import json

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module='google.protobuf')

# Load environment variables
load_dotenv()

# --- Constants ---
ASPECT_RATIO = 9 / 16

GEMINI_PROMPT_TEMPLATE = """
You are a senior short-form video editor. Read the ENTIRE transcript and word-level timestamps to choose the 3–15 MOST VIRAL moments for TikTok/IG Reels/YouTube Shorts. Each clip must be between 15 and 60 seconds long.

⚠️ FFMPEG TIME CONTRACT — STRICT REQUIREMENTS:
- Return timestamps in ABSOLUTE SECONDS from the start of the video (usable in: ffmpeg -ss <start> -to <end> -i <input> ...).
- Only NUMBERS with decimal point, up to 3 decimals (examples: 0, 1.250, 17.350).
- Ensure 0 ≤ start < end ≤ VIDEO_DURATION_SECONDS.
- Each clip between 15 and 60 s (inclusive).
- Prefer starting 0.2–0.4 s BEFORE the hook and ending 0.2–0.4 s AFTER the payoff.
- Use silence moments for natural cuts; never cut in the middle of a word or phrase.
- STRICTLY FORBIDDEN to use time formats other than absolute seconds.

VIDEO_DURATION_SECONDS: {video_duration}

TRANSCRIPT_TEXT (raw):
{transcript_text}

WORDS_JSON (array of {{w, s, e}} where s/e are seconds):
{words_json}

STRICT EXCLUSIONS:
- No generic intros/outros or purely sponsorship segments unless they contain the hook.
- No clips < 15 s or > 60 s.

OUTPUT — RETURN ONLY VALID JSON (no markdown, no comments). Order clips by predicted performance (best to worst). In the descriptions, ALWAYS include a CTA like "Follow me and comment X and I'll send you the workflow" (especially if discussing an n8n workflow):
{{
  "shorts": [
    {{
      "start": <number in seconds, e.g., 12.340>,
      "end": <number in seconds, e.g., 37.900>,
      "video_description_for_tiktok": "<description for TikTok oriented to get views>",
      "video_description_for_instagram": "<description for Instagram oriented to get views>",
      "video_title_for_youtube_short": "<title for YouTube Short oriented to get views 100 chars max>",
      "viral_hook_text": "<SHORT punchy text overlay (max 10 words) with 1-2 fitting emojis. MUST BE IN THE SAME LANGUAGE AS THE VIDEO TRANSCRIPT. Examples: 'POV: You realized... 😳', 'Did you know? 🤯', 'Stop doing this! 🚫'>"
    }}
  ]
}}
"""

# Load the YOLO model once (Keep for backup or scene analysis if needed).
# YOLO_MODEL_PATH lets deployments point at a pre-downloaded weights file so a
# volume mounted over the workdir doesn't trigger a re-download at startup.
#
# Ultralytics defaults to CPU when no device is passed, even when a CUDA
# runtime is visible. Keep CPU as the safe default stack, but make the GPU
# overlay explicit so the fallback detector does not silently stay on CPU.
_yolo_device = os.environ.get("YOLO_DEVICE", "auto").strip().lower()
if _yolo_device in {"", "auto"}:
    _yolo_device = "cuda" if torch.cuda.is_available() else "cpu"
elif _yolo_device == "cuda" and not torch.cuda.is_available():
    raise RuntimeError(
        "YOLO_DEVICE=cuda was requested, but torch.cuda.is_available() is false"
    )
model = YOLO(os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"))
model.to(_yolo_device)
print(f"⚙️ YOLO device: {_yolo_device}", flush=True)

# --- MediaPipe Setup ---
# Use standard Face Detection (BlazeFace) for speed
mp_face_detection = mp.solutions.face_detection
face_detection = mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)

# Consecutive detections a large target move must survive before the camera
# follows it (see SmoothedCameraman.update_target). Env-overridable so the
# damping can be dialled back without a deploy; 1 restores the old behaviour.
JUMP_CONFIRM_FRAMES = max(int(os.environ.get("JUMP_CONFIRM_FRAMES", "3")), 1)

# The detector is intentionally sampled every few frames. A confirmed target
# is still the source of truth, but filtering it before the camera follows it
# keeps one-pixel face-box changes from becoming visible micro-pans. The
# values are conservative: the existing safe zone still prevents the camera
# from chasing normal head movement, while the speed/acceleration limits keep
# a real subject move in frame without snapping.
try:
    TARGET_SMOOTHING = min(max(float(os.environ.get("TARGET_SMOOTHING", "0.32")), 0.05), 1.0)
except ValueError:
    TARGET_SMOOTHING = 0.32
try:
    CAMERA_MAX_SPEED = min(max(float(os.environ.get("CAMERA_MAX_SPEED", "12.0")), 1.0), 30.0)
except ValueError:
    CAMERA_MAX_SPEED = 12.0
try:
    CAMERA_ACCELERATION = min(max(float(os.environ.get("CAMERA_ACCELERATION", "0.30")), 0.05), 1.0)
except ValueError:
    CAMERA_ACCELERATION = 0.30


class SmoothedCameraman:
    """
    Handles smooth camera movement.
    Simplified Logic: "Heavy Tripod"
    Only moves if the subject leaves the center safe zone.
    Moves slowly and linearly.
    """
    def __init__(self, output_width, output_height, video_width, video_height, aspect_ratio=ASPECT_RATIO):
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        self.aspect_ratio = aspect_ratio

        # Initial State
        self.current_center_x = video_width / 2
        self.target_center_x = video_width / 2
        self.filtered_target_center_x = self.target_center_x
        self._camera_step = 0.0

        # Calculate crop dimensions once
        self.crop_height = video_height
        self.crop_width = int(self.crop_height * aspect_ratio)
        if self.crop_width > video_width:
             self.crop_width = video_width
             self.crop_height = int(self.crop_width / aspect_ratio)
             
        # Safe Zone: 20% of the video width
        # As long as the target is within this zone relative to current center, DO NOT MOVE.
        self.safe_zone_radius = self.crop_width * 0.25

        # A target that teleports further than the safe zone in one detection is
        # far more often a detector error — a second face, a false positive, a
        # box snapping to a different body part — than a person who actually
        # moved that far. Committing to it immediately is what made the camera
        # swing: measured on real user footage, 22% of target updates jumped
        # more than the entire safe zone. So a big move has to REPEAT this many
        # times before the camera follows it; a wrong reading disappears on the
        # next detection and never moves the frame.
        #
        # The cost is latency on a genuinely fast move: at DETECT_STRIDE=4 and
        # 30fps, three confirmations is ~0.4s. That reads as an operator being
        # unhurried, which is the look we want, and it is far cheaper than the
        # whip-panning it replaces.
        #
        # Measured over 262s of TRACK footage from two real user videos
        # (26-jul-2026), confirm=1 -> 3: in-scene reversals 0.41/s -> 0.13/s
        # (-69%), camera travel 91px/s -> 60px/s (-34%). Per scene, 54 of 84 get
        # calmer and 23 are unchanged — but 7 get BUSIER, up to 59 -> 108px/s,
        # because committing later can leave the camera further to travel. Net
        # strongly positive, not universally so.
        self.jump_confirm_frames = JUMP_CONFIRM_FRAMES
        self._pending_target = None
        self._pending_count = 0

    def update_target(self, face_box):
        """Update the target centre from a detection, ignoring lone big jumps."""
        if not face_box:
            return
        x, y, w, h = face_box
        new_center = x + w / 2

        if abs(new_center - self.target_center_x) > self.safe_zone_radius:
            # Same big move as last time? Count it. Otherwise start counting
            # afresh — two contradictory outliers must not confirm each other.
            if (self._pending_target is not None
                    and abs(new_center - self._pending_target) <= self.safe_zone_radius):
                self._pending_count += 1
            else:
                self._pending_target = new_center
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return  # not convinced yet — hold the frame

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center
    
    def get_crop_box(self, force_snap=False):
        """
        Returns the (x1, y1, x2, y2) for the current frame.
        """
        if force_snap:
            self.current_center_x = self.target_center_x
            self.filtered_target_center_x = self.target_center_x
            self._camera_step = 0.0
        else:
            # Filter the confirmed target separately from target_center_x. The
            # latter remains immediate so the existing jump confirmation and
            # tracker hysteresis contracts do not change.
            self.filtered_target_center_x += (
                self.target_center_x - self.filtered_target_center_x
            ) * TARGET_SMOOTHING
            diff = self.filtered_target_center_x - self.current_center_x

            # Keep the safe-zone deadband, then ease the camera step toward a
            # bounded speed. This removes the old 3px/15px step change that
            # made the crop visibly jerk whenever a detection crossed the
            # threshold.
            if abs(diff) > self.safe_zone_radius:
                desired_step = min(CAMERA_MAX_SPEED,
                                   max(1.0, abs(diff) * 0.16))
            else:
                desired_step = 0.0
            self._camera_step += (
                desired_step - self._camera_step
            ) * CAMERA_ACCELERATION

            if abs(diff) > self.safe_zone_radius and self._camera_step > 0:
                direction = 1 if diff > 0 else -1
                step = min(abs(diff), self._camera_step)
                self.current_center_x += direction * step

                # Check if we overshot (prevent oscillation).
                new_diff = self.filtered_target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.filtered_target_center_x
                
        # Clamp center
        half_crop = self.crop_width / 2
        
        if self.current_center_x - half_crop < 0:
            self.current_center_x = half_crop
        if self.current_center_x + half_crop > self.video_width:
            self.current_center_x = self.video_width - half_crop
            
        x1 = int(self.current_center_x - half_crop)
        x2 = int(self.current_center_x + half_crop)
        
        x1 = max(0, x1)
        x2 = min(self.video_width, x2)
        
        y1 = 0
        y2 = self.video_height
        
        return x1, y1, x2, y2

class SpeakerTracker:
    """
    Tracks speakers over time to prevent rapid switching and handle temporary obstructions.
    """
    def __init__(self, stabilization_frames=15, cooldown_frames=30):
        self.active_speaker_id = None
        self.speaker_scores = {}  # {id: score}
        self.last_seen = {}       # {id: frame_number}
        self.locked_counter = 0   # How long we've been locked on current speaker
        
        # Hyperparameters
        self.stabilization_threshold = stabilization_frames # Frames needed to confirm a new speaker
        self.switch_cooldown = cooldown_frames              # Minimum frames before switching again
        self.last_switch_frame = -1000
        
        # ID tracking
        self.next_id = 0
        self.known_faces = [] # [{'id': 0, 'center': x, 'last_frame': 123}]

    def get_target(self, face_candidates, frame_number, width):
        """
        Decides which face to focus on.
        face_candidates: list of {'box': [x,y,w,h], 'score': float}
        """
        current_candidates = []
        
        # 1. Match faces to known IDs (simple distance tracking)
        for face in face_candidates:
            x, y, w, h = face['box']
            center_x = x + w / 2
            
            best_match_id = -1
            min_dist = width * 0.15 # Reduced matching radius to avoid jumping in groups
            
            # Try to match with known faces seen recently
            for kf in self.known_faces:
                if frame_number - kf['last_frame'] > 30: # Forgot faces older than 1s (was 2s)
                    continue
                    
                dist = abs(center_x - kf['center'])
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = kf['id']
            
            # If no match, assign new ID
            if best_match_id == -1:
                best_match_id = self.next_id
                self.next_id += 1
            
            # Update known face
            self.known_faces = [kf for kf in self.known_faces if kf['id'] != best_match_id]
            self.known_faces.append({'id': best_match_id, 'center': center_x, 'last_frame': frame_number})
            
            current_candidates.append({
                'id': best_match_id,
                'box': face['box'],
                'score': face['score']
            })

        # 2. Update Scores with decay
        for pid in list(self.speaker_scores.keys()):
             self.speaker_scores[pid] *= 0.85 # Faster decay (was 0.9)
             if self.speaker_scores[pid] < 0.1:
                 del self.speaker_scores[pid]

        # Add new scores
        for cand in current_candidates:
            pid = cand['id']
            # Score is purely based on size (proximity) now that we don't have mouth
            raw_score = cand['score'] / (width * width * 0.05)
            self.speaker_scores[pid] = self.speaker_scores.get(pid, 0) + raw_score

        # 3. Determine Best Speaker
        if not current_candidates:
            # If no one found, maintain last active speaker if cooldown allows
            # to avoid black screen or jump to 0,0
            return None 
            
        best_candidate = None
        max_score = -1
        
        for cand in current_candidates:
            pid = cand['id']
            total_score = self.speaker_scores.get(pid, 0)
            
            # Hysteresis: HUGE Bonus for current active speaker
            if pid == self.active_speaker_id:
                total_score *= 3.0 # Sticky factor
                
            if total_score > max_score:
                max_score = total_score
                best_candidate = cand

        # 4. Decide Switch
        if best_candidate:
            target_id = best_candidate['id']
            
            if target_id == self.active_speaker_id:
                self.locked_counter += 1
                return best_candidate['box']
            
            # New person. The cooldown must hold whether or not the current
            # speaker happens to be detected in THIS frame.
            #
            # It used to fall through and switch when the active speaker was
            # missing from the candidate list — a blink, a head turn or one
            # motion-blurred frame was enough. That is precisely when the
            # cooldown is needed, so it only ever fired when it wasn't: 3 of 7
            # target switches measured on a 12s clip (25-jul-2026) jumped the
            # cooldown this way, and every jump drags the camera across frame.
            #
            # Returning None holds instead: the caller only calls
            # update_target() on a truthy box, so the camera keeps its current
            # target and finishes whatever move it was making. The hold is
            # bounded by the cooldown itself — once it expires, a speaker who
            # really did leave the shot is switched away from normally.
            if frame_number - self.last_switch_frame < self.switch_cooldown:
                old_cand = next((c for c in current_candidates if c['id'] == self.active_speaker_id), None)
                return old_cand['box'] if old_cand else None

            self.active_speaker_id = target_id
            self.last_switch_frame = frame_number
            self.locked_counter = 0
            return best_candidate['box']
            
        return None

# Detectors never need full-resolution frames: MediaPipe returns relative
# coords and YOLO boxes are scaled back up. Running them on a ≤640px copy cuts
# per-frame preprocessing cost hard, which is what dominates CPU-only renders.
DETECT_MAX_WIDTH = 640
# The global MediaPipe graph and YOLO model are NOT thread-safe; clips render
# in parallel, so every inference goes through this lock. Contention is small
# (a few ms per call) — the ffmpeg renders are where the parallel time goes.
DETECT_LOCK = threading.Lock()
# Detect every Nth frame; SmoothedCameraman interpolates between updates.
DETECT_STRIDE = max(int(os.environ.get("DETECT_STRIDE", "4")), 1)
# YOLO fallback (no face found) is far heavier than MediaPipe — extra throttle.
YOLO_FALLBACK_STRIDE = DETECT_STRIDE * 2


def _detection_frame(frame):
    """Downscaled copy for detectors. Returns (small_frame, scale) with
    scale mapping small-frame pixel coords back to the original frame."""
    h, w = frame.shape[:2]
    if w <= DETECT_MAX_WIDTH:
        return frame, 1.0
    scale = w / DETECT_MAX_WIDTH
    small = cv2.resize(frame, (DETECT_MAX_WIDTH, max(int(h / scale), 2)),
                       interpolation=cv2.INTER_AREA)
    return small, scale


def detect_face_candidates(frame):
    """
    Returns list of all detected faces using lightweight FaceDetection.
    Boxes are in ORIGINAL frame coordinates (detection runs downscaled;
    MediaPipe's relative coords make the mapping exact).
    """
    height, width, _ = frame.shape
    small, _scale = _detection_frame(frame)
    rgb_frame = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    with DETECT_LOCK:
        results = face_detection.process(rgb_frame)
    
    candidates = []
    
    if not results.detections:
        return []
        
    for detection in results.detections:
        bboxC = detection.location_data.relative_bounding_box
        x = int(bboxC.xmin * width)
        y = int(bboxC.ymin * height)
        w = int(bboxC.width * width)
        h = int(bboxC.height * height)
        
        candidates.append({
            'box': [x, y, w, h],
            'score': w * h # Area as score
        })
            
    return candidates

def detect_person_yolo(frame):
    """
    Fallback: Detect largest person using YOLO when face detection fails.
    Returns [x, y, w, h] of the person's 'upper body' approximation, in
    ORIGINAL frame coordinates (inference runs on a downscaled copy).
    """
    small, scale = _detection_frame(frame)
    # Use the globally loaded model
    with DETECT_LOCK:
        results = model(small, verbose=False, classes=[0]) # class 0 is person

    if not results:
        return None

    best_box = None
    max_area = 0

    for result in results:
        boxes = result.boxes
        for box in boxes:
            x1, y1, x2, y2 = [int(i * scale) for i in box.xyxy[0]]
            w = x2 - x1
            h = y2 - y1
            area = w * h
            
            if area > max_area:
                max_area = area
                # Focus on the top 40% of the person (head/chest) for framing
                # This approximates where the face is if we can't detect it directly
                face_h = int(h * 0.4)
                best_box = [x1, y1, w, face_h]
                
    return best_box

def create_general_frame(frame, output_width, output_height):
    """
    Creates a 'General Shot' frame: 
    - Background: Blurred zoom of original
    - Foreground: Original video scaled to fit width, centered vertically.
    """
    orig_h, orig_w = frame.shape[:2]
    
    # 1. Background (Fill Height)
    # Crop center to aspect ratio
    bg_scale = output_height / orig_h
    bg_w = int(orig_w * bg_scale)
    bg_resized = cv2.resize(frame, (bg_w, output_height), interpolation=cv2.INTER_LINEAR)

    # Crop center of background
    start_x = (bg_w - output_width) // 2
    if start_x < 0: start_x = 0
    background = bg_resized[:, start_x:start_x+output_width]
    if background.shape[1] != output_width:
        background = cv2.resize(background, (output_width, output_height), interpolation=cv2.INTER_LINEAR)

    # Blur background: blur at quarter resolution and scale back up — visually
    # identical for a defocused backdrop, an order of magnitude cheaper than a
    # 51px Gaussian at full size.
    small_bg = cv2.resize(background, (max(output_width // 4, 2), max(output_height // 4, 2)),
                          interpolation=cv2.INTER_AREA)
    small_bg = cv2.GaussianBlur(small_bg, (13, 13), 0)
    background = cv2.resize(small_bg, (output_width, output_height),
                            interpolation=cv2.INTER_LINEAR)

    # 2. Foreground (Fit Width)
    scale = output_width / orig_w
    fg_h = int(orig_h * scale)
    foreground = cv2.resize(frame, (output_width, fg_h), interpolation=cv2.INTER_LINEAR)
    
    # 3. Overlay
    y_offset = (output_height - fg_h) // 2
    
    # Clone background to avoid modifying it
    final_frame = background.copy()
    final_frame[y_offset:y_offset+fg_h, :] = foreground
    
    return final_frame

# NOTE: a "route text-heavy scenes to GENERAL" rule was tried here and removed
# on 26-jul-2026. The problem it targets is real — a screencast that happens to
# contain one face gets cropped to the face and its headlines come out cut
# mid-word — but edge density is the wrong signal for it. Measured: a
# constructed talking-head-beside-a-chart scored 0.012 while the SAME shot
# without the panels scored 0.029, because a flat panel of text has far fewer
# edges than ordinary scene detail. Canny measures visual busyness, not text.
# A real fix needs an actual text detector (MSER/EAST) validated against clips
# that contain the failure mode; this corpus has almost none.


def analyze_scenes_strategy(video_path, scenes):
    """
    Analyzes each scene to determine if it should be TRACK (Single person) or GENERAL (Group/Wide).
    Returns list of strategies corresponding to scenes.
    """
    cap = cv2.VideoCapture(video_path)
    strategies = []

    if not cap.isOpened():
        return ['TRACK'] * len(scenes)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    for start, end in tqdm(scenes, desc="   Analyzing Scenes"):
        s_f, e_f = start.get_frames(), end.get_frames()
        # Sample 5 frames spread across the scene, clamped inside it (the old
        # start+5/end-5 samples landed outside scenes shorter than ~10 frames).
        margin = min(2, max(0, (e_f - s_f - 1) // 2))
        frames_to_check = sorted(set(
            int(round(f)) for f in np.linspace(s_f + margin, e_f - 1 - margin, 5)
        ))

        face_counts = []
        for f_idx in frames_to_check:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret: continue

            # Near-black frames (fades, cut-to-black) carry no faces and used
            # to drag single-person scenes into GENERAL. Skip them.
            if frame.mean() < 16:
                continue

            # Detect faces
            candidates = detect_face_candidates(frame)
            face_counts.append(len(candidates))

        # Decision Logic
        if not face_counts:
            avg_faces = 0
        else:
            avg_faces = sum(face_counts) / len(face_counts)

        # Strategy:
        # 0 faces -> GENERAL (Landscape/B-roll)
        # 1 face -> TRACK
        # > 1.2 faces -> GENERAL (Group)

        if avg_faces > 1.2 or avg_faces < 0.5:
            strategies.append('GENERAL')
        else:
            strategies.append('TRACK')

    cap.release()

    # Hysteresis: a short scene whose two neighbors agree on the opposite
    # strategy is almost always a sampling miss (profile face, insert shot).
    # Each TRACK<->GENERAL flip is a full on-screen layout change, so flapping
    # is worse than an occasional wrong-but-stable choice.
    max_flip_frames = int(2.0 * fps)
    for i in range(1, len(strategies) - 1):
        dur = scenes[i][1].get_frames() - scenes[i][0].get_frames()
        if (dur < max_flip_frames
                and strategies[i - 1] == strategies[i + 1] != strategies[i]):
            strategies[i] = strategies[i - 1]

    return strategies

def detect_scenes(video_path):
    import scene_detection
    return scene_detection.detect_scenes(video_path)

def get_video_resolution(video_path):
    probe = cv2.VideoCapture(video_path)
    try:
        if not probe.isOpened():
            raise IOError(f"cannot open video: {video_path}")
        return (int(probe.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        probe.release()


# Byte budget for the sanitized video title used as the stem of every derived
# file. Filesystems cap a name in BYTES (255 on ext4), not characters, and the
# pipeline decorates this stem: "_clip_10.mp4" (12), "subtitled_<ts>_" (21),
# "hooked_<ts>_" (18), "temp_hook_<hex8>_" (19), "autosubs_<ts>_" + ".ass" (24).
# Budgeting 120 bytes leaves room for all of them stacked (worst chain:
# subtitled_<ts>_hooked_<ts>_<stem>_clip_NN.mp4 ≈ 171 bytes) under the limit.
#
# The old cap was 100 CHARACTERS, which is 300 bytes of Bengali or Arabic — over
# the limit before any decoration. It surfaced as OSError 36 killing the hook
# endpoint in prod on 26-jul-2026.
MAX_TITLE_BYTES = 120


def truncate_bytes(text, max_bytes):
    """Trim ``text`` to a byte budget without splitting a multi-byte character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def sanitize_filename(filename):
    """Remove invalid characters from filename and bound it for the filesystem."""
    filename = re.sub(r'[<>:"/\\|?*#]', '', filename)
    filename = filename.replace(' ', '_')
    return truncate_bytes(filename, MAX_TITLE_BYTES)


def plan_download_attempts(direct_first, statics, paid, have_hd):
    """Ordered (label, capped, proxy) download plan — pure, unit-tested.

    Cheapest bandwidth first: the server's own IP, then the flat-rate static
    ISP proxies (uncapped 1440p, free bytes), then the per-GB paid proxy
    (720p cost cap), and last the conservative fallback strategy through the
    paid proxy (or a static/direct when no paid proxy is configured).
    ``capped`` marks attempts whose bytes are billed per GB."""
    plan = []
    if direct_first:
        plan.append(('HD-direct', False, None))
    if have_hd:
        for i, s in enumerate(statics):
            plan.append((f'HD-static{i + 1}', False, s))
        plan.append(('HD', bool(paid), paid))
    plan.append(('fallback', bool(paid),
                 paid if paid else (statics[0] if statics else None)))
    return plan


def download_youtube_video(url, output_dir="."):
    """
    Downloads a YouTube video using yt-dlp.
    Returns the path to the downloaded video and the video title.
    """
    # SSRF guard: block non-http(s) schemes and private/loopback/metadata hosts
    # before handing the URL to yt-dlp.
    from security_utils import assert_public_url
    assert_public_url(url)

    print(f"🔍 Debug: yt-dlp version: {yt_dlp.version.__version__}")
    print("📥 Downloading video from YouTube...")
    step_start_time = time.time()

    cookies_path = '/app/cookies.txt'
    cookies_env = os.environ.get("YOUTUBE_COOKIES")
    if cookies_env:
        print("🍪 Found YOUTUBE_COOKIES env var, creating cookies file inside container...")
        try:
            with open(cookies_path, 'w') as f:
                f.write(cookies_env)
            if os.path.exists(cookies_path):
                 # Never print file CONTENT here: with a headerless cookies
                 # blob this would leak live YouTube session cookies to logs.
                 print(f"   Debug: Cookies file created. Size: {os.path.getsize(cookies_path)} bytes")
        except Exception as e:
            print(f"⚠️ Failed to write cookies file: {e}")
            cookies_path = None
    else:
        cookies_path = None
        print("⚠️ YOUTUBE_COOKIES env var not found.")
    
    # Optional HTTP proxy. Set PROXY_URL to route downloads through it; unset
    # (self-host) goes direct as before.
    _proxy = os.environ.get("PROXY_URL", "").strip() or None
    if _proxy:
        print("🌐 Using proxy for download.")

    # Flat-rate static ISP proxies (STATIC_PROXY_URLS, comma-separated), tried
    # BEFORE the per-GB proxy: dedicated IPs with unlimited traffic, so their
    # bandwidth costs nothing per job and carries no 720p cost cap. Rotated per
    # job to spread load (and YouTube's attention) across the pool. PROXY_URL
    # stays the paid last resort — with STATIC_PROXY_URLS unset the behavior is
    # byte-identical to before.
    _statics = [p.strip() for p in
                os.environ.get("STATIC_PROXY_URLS", "").split(",") if p.strip()]
    if _statics:
        import random as _random
        k = _random.randrange(len(_statics))
        _statics = _statics[k:] + _statics[:k]
        print(f"🌐 {len(_statics)} static ISP proxies configured.")

    # Two download strategies, tried in order so a break in the HD path degrades
    # gracefully instead of failing the whole job: an HD attempt first, then a
    # conservative fallback (also the only strategy for self-host).
    _bgutil_http = os.environ.get("BGUTIL_BASE_URL", "").strip()
    _bgutil_script = os.environ.get("BGUTIL_SCRIPT_PATH", "").strip()
    if _bgutil_http:
        hd_args = {'youtubepot-bgutilhttp': {'base_url': [_bgutil_http]}}
    elif _bgutil_script:
        hd_args = {'youtubepot-bgutilscript': {'script_path': [_bgutil_script]}}
    else:
        hd_args = None
    fallback_args = {
        'youtube': {
            'player_client': ['tv_embed', 'android', 'mweb', 'web'],
            'player_skip': ['webpage', 'configs'],
        }
    }

    # Cap at 720p ONLY when the bytes actually go through the PER-GB paid proxy
    # — that cap exists to control bandwidth cost, and the direct attempt and
    # the flat-rate static proxies have none.
    #
    # This is per-attempt on purpose. Deciding it once from `_proxy` capped the
    # DIRECT attempt too, so with DIRECT_FIRST=1 (which serves most downloads)
    # every YouTube source arrived at 720p and, since the reframe inherits the
    # source height, 80% of delivered clips came out 406x720 (audited 25-jul-2026).
    def _hd_fmt_for(capped):
        if capped:
            return ('bestvideo[vcodec^=avc1][height<=720][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[vcodec^=avc1][height<=720]+bestaudio/'
                    'best[height<=720][ext=mp4]/best[height<=720]/best')
        return ('bestvideo[vcodec^=avc1][height<=1440][ext=mp4]+bestaudio[ext=m4a]/'
                'bestvideo[vcodec^=avc1][height<=1440]+bestaudio/'
                'best[height<=1440][ext=mp4]/best[ext=mp4]/best')
    fallback_fmt = 'best[ext=mp4]/best'

    def _base_opts(extractor_args, proxy):
        return {
            'quiet': False, 'verbose': True, 'no_warnings': False,
            'cookiefile': cookies_path if cookies_path else None,
            'proxy': proxy, 'socket_timeout': 30, 'retries': 10, 'fragment_retries': 10,
            'nocheckcertificate': True, 'cachedir': False,
            'extractor_args': extractor_args,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ),
            },
        }

    # Wire bytes actually pulled through the (paid) proxy, summed across
    # fragments/streams. Reported to app.py via the PROXY_BYTES= line below.
    _dl_bytes = {"total": 0}

    def _progress_hook(d):
        if d.get('status') == 'finished':
            _dl_bytes["total"] += int(d.get('total_bytes')
                                      or d.get('total_bytes_estimate')
                                      or d.get('downloaded_bytes') or 0)

    def _attempt(extractor_args, fmt, proxy):
        _dl_bytes["total"] = 0
        with yt_dlp.YoutubeDL(_base_opts(extractor_args, proxy)) as ydl:
            info = ydl.extract_info(url, download=False)
        sanitized = sanitize_filename(info.get('title', 'youtube_video'))
        expected = os.path.join(output_dir, f'{sanitized}.mp4')
        if os.path.exists(expected):
            os.remove(expected)
        dl_opts = {
            **_base_opts(extractor_args, proxy),
            'format': fmt,
            'outtmpl': os.path.join(output_dir, f'{sanitized}.%(ext)s'),
            'merge_output_format': 'mp4', 'overwrites': True,
            'progress_hooks': [_progress_hook],
        }
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([url])
        return sanitized

    # DIRECT_FIRST=1: try the server's own IP before spending proxy bandwidth.
    # Needs cookies + a PO-token provider — without both, YouTube flags the
    # datacenter IP after the first request (verified in prod, 21-jul-2026).
    _direct_first = (os.environ.get("DIRECT_FIRST", "").strip() == "1"
                     and (_proxy or _statics) and hd_args and cookies_path)

    attempts = [
        (label,
         fallback_args if label == 'fallback' else hd_args,
         fallback_fmt if label == 'fallback' else _hd_fmt_for(capped),
         proxy)
        for label, capped, proxy in plan_download_attempts(
            _direct_first, _statics, _proxy, bool(hd_args))
    ]

    sanitized_title = None
    last_err = None
    used_proxy = False
    for label, ea, fmt, proxy in attempts:
        # A 403 on the media fetch is usually transient: the googlevideo URL is
        # bound to the IP that extracted it, and the residential proxy rotates
        # its exit IP between requests. Retrying re-extracts and usually lands
        # on a consistent IP (3 of 62 downloads hit this on 22-jul-2026).
        for retry in range(2):
            try:
                print(f"📥 Download attempt: {label}" + (f" (retry {retry})" if retry else ""))
                sanitized_title = _attempt(ea, fmt, proxy)
                # Only bytes through the PER-GB proxy cost money; direct and
                # the flat-rate static proxies are free bandwidth for the
                # monthly counter's purposes.
                used_proxy = proxy is not None and proxy == _proxy
                print(f"✅ Download succeeded ({label}).")
                break
            except Exception as e:
                last_err = e
                print(f"⚠️  Download attempt '{label}' failed: {str(e)[:200]}")
                retryable = '403' in str(e) or 'Forbidden' in str(e)
                if not retryable or retry == 1:
                    break
                time.sleep(3)
        if sanitized_title is not None:
            break

    if sanitized_title is None:
        import sys
        error_msg = f"""
❌ ================================================================= ❌
❌ FATAL ERROR: YOUTUBE DOWNLOAD FAILED (all strategies)
❌ ================================================================= ❌
REASON: YouTube blocked the request or the download tooling is out of date.
👇 SOLUTION FOR USER: download the video manually and use the 'Upload Video' tab.
Technical Details: {str(last_err)}
"""
        print(error_msg, file=sys.stdout)
        print(error_msg, file=sys.stderr)
        sys.stdout.flush(); sys.stderr.flush()
        time.sleep(0.5)
        raise last_err

    downloaded_file = os.path.join(output_dir, f'{sanitized_title}.mp4')
    if not os.path.exists(downloaded_file):
        for f in os.listdir(output_dir):
            if f.startswith(sanitized_title) and f.endswith('.mp4'):
                downloaded_file = os.path.join(output_dir, f)
                break

    if used_proxy and _dl_bytes["total"]:
        # Machine-parseable marker consumed by app.py's log reader for the
        # monthly proxy-bandwidth counter. Not shown to clients (log filter).
        # Only emitted when the winning attempt actually went through the
        # proxy — direct-first successes are free bandwidth.
        print(f"PROXY_BYTES={_dl_bytes['total']}")
    print(f"✅ Video downloaded in {time.time() - step_start_time:.2f}s: {downloaded_file}")
    return downloaded_file, sanitized_title

def finalize_clip_passthrough(input_video, final_output_video):
    """Keep the clip's native framing (for horizontal/16:9 output).

    The input is the freshly encoded cut, so a stream-copy remux is enough to
    add +faststart — re-encoding here would only cost time and quality.
    """
    if os.path.exists(final_output_video):
        os.remove(final_output_video)
    print(f"🎬 Passthrough (native framing): {input_video}")
    cmd = [
        'ffmpeg', '-y', '-i', input_video,
        '-c', 'copy', *METADATA_SCRUB, '-movflags', '+faststart',
        final_output_video,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    print(f"✅ Clip saved to {final_output_video}")
    return True


def auto_caption_clip(clip_path, transcript, clip_start, clip_end,
                      subtitle_config=None):
    """Burn the default caption style onto a finished clip.

    Captions are mandatory for short-form to land, but they were opt-in behind a
    modal and only 9% of delivered clips ever got them (prod audit, 25-jul-2026).
    So every clip now ships captioned by default.

    The captioned file is written ALONGSIDE the clip as
    ``subtitled_<ts>_<clip>.mp4`` — the same convention /api/subtitle uses — so
    the untouched original stays on disk and re-styling from the modal replaces
    the captions instead of burning a second layer over them.

    Returns a render artifact ``{"path", "subtitle_config", "revision"}``,
    or None when captions were skipped (silent video, no words in range,
    AUTO_CAPTIONS=0, or any failure — a caption problem must never cost the
    user the clip they already paid for). The structured result is important:
    callers must persist the exact file and recipe instead of guessing the
    newest derivative from a directory listing.
    """
    if os.environ.get("AUTO_CAPTIONS", "1").strip() == "0":
        return None
    if not transcript or not transcript.get('segments'):
        return None  # silent video: nothing to caption
    try:
        import subtitles as _subs
        recipe = _subs.canonical_subtitle_config(subtitle_config)
        style = _subs.subtitle_render_options(recipe)
        render_style, render_effect = _subs.resolve_render_style(
            recipe["style"], recipe["animation"], recipe["effect"])
        output_dir = os.path.dirname(clip_path)
        stem = os.path.basename(clip_path)
        generation_id = time.time_ns()

        render_transcript = transcript
        if recipe.get("captions"):
            edited = [
                {"word": " " + str(word.get("text", "")).strip(),
                 "start": max(0.0, float(word.get("startMs", 0)) / 1000.0),
                 "end": max(0.0, float(word.get("endMs", 0)) / 1000.0)}
                for word in recipe["captions"]
                if str(word.get("text", "")).strip()
                and float(word.get("endMs", 0)) > float(word.get("startMs", 0))
            ]
            if edited:
                render_transcript = {
                    "language": (transcript or {}).get("language", "en"),
                    "segments": [{
                        "start": edited[0]["start"],
                        "end": edited[-1]["end"],
                        "text": " ".join(w["word"].strip() for w in edited),
                        "words": edited,
                    }],
                }
                clip_start, clip_end = 0.0, edited[-1]["end"]
        # The output name MUST stay exactly "subtitled_<ts>_<clip filename>":
        # the modal's walk-back and _canonical_clip_file both reconstruct the
        # clean original from it, so trimming the stem here would orphan the
        # pair. Length is bounded upstream instead, by MAX_TITLE_BYTES at
        # download time. A legacy clip whose name predates that budget can still
        # overflow — that raises OSError 36, which the except below turns into
        # "ship the clip uncaptioned" rather than a broken filename.
        # The .ass path is interpolated INTO an ffmpeg filter string
        # (-vf ass='...'), where a literal apostrophe closes the quote and
        # breaks the filter. Titles carry apostrophes constantly in English
        # ("Earth's", "Don't"), so this name must stay free of the clip stem —
        # which is exactly why /api/subtitle has always used a neutral
        # "subs_<i>_<ts>.ass". Deriving it from the stem silently cost captions
        # on every apostrophe title until 29-jul-2026.
        #
        # The OUTPUT name still carries the stem, and must: the modal's
        # walk-back and _canonical_clip_file reconstruct the clean original
        # from it. That one is only ever passed as an argv element, never
        # inside a filter string, so quoting never applies to it.
        # Unique per clip, not just per second: clips render in parallel
        # (CLIP_WORKERS), so a bare timestamp would collide and let one clip
        # burn another's captions.
        subtitle_ext = "ass" if render_style == "karaoke" else "srt"
        ass_path = os.path.join(
            output_dir, f"autosubs_{generation_id}_{uuid.uuid4().hex[:8]}.{subtitle_ext}")
        out_path = os.path.join(output_dir, f"subtitled_{generation_id}_{stem}")

        if render_style == "karaoke":
            generated = _subs.generate_ass(
                render_transcript, clip_start, clip_end, ass_path,
                max_chars=style["max_chars"], max_duration=style["max_duration"],
                alignment=style["alignment"], fontsize=style["font_size"],
                font_name=style["font_name"], font_color=style["font_color"],
                border_color=style["border_color"], border_width=style["border_width"],
                highlight_color=style["highlight_color"], effect=render_effect,
                base_opacity=style["base_opacity"], uppercase=style["uppercase"],
                bg_color=style["bg_color"], bg_opacity=style["bg_opacity"])
        else:
            generated = _subs.generate_srt(
                render_transcript, clip_start, clip_end, ass_path,
                max_chars=style["max_chars"], max_duration=style["max_duration"])
        if not generated:
            print("   ℹ️ No words in range — clip ships without captions.")
            return None

        _subs.burn_subtitles(
            clip_path, ass_path, out_path,
            alignment=style["alignment"], fontsize=style["font_size"],
            font_name=style["font_name"], font_color=style["font_color"],
            border_color=style["border_color"], border_width=style["border_width"],
            bg_color=style["bg_color"], bg_opacity=style["bg_opacity"])
        print(f"   💬 Captions burned: {os.path.basename(out_path)}")
        return {
            "path": out_path,
            "server_file": os.path.basename(out_path),
            "subtitle_config": recipe,
            "revision": str(generation_id),
        }
    except Exception as e:
        print(f"   ⚠️ Auto-captions failed ({type(e).__name__}: {e}) — "
              f"delivering the clip without them.")
        return None


def auto_hook_clip(clip_path, clip):
    """Burn the clip's Gemini hook text as a DERIVED file (AUTO_HOOK=1).

    Writes ``hooked_<ts>_<clip filename>`` next to the canonical clip, exactly
    like captions write ``subtitled_<ts>_...``: the canonical stays clean, so
    the hook can later be replaced or removed by walking the prefix back
    (app.py `_strip_burned_hook`). Captions are then burned ON TOP of the
    hooked file, keeping the "captions are always the last layer" invariant.

    Returns (hooked_path, hook_config), or None when skipped or failed — a
    hook problem must never cost the user the clip itself (same fail-open
    contract as auto_caption_clip)."""
    text = (clip.get('viral_hook_text') or '').strip()
    if not text:
        return None
    style = os.environ.get("AUTO_HOOK_STYLE", "classic")
    try:
        seconds = float(os.environ.get("AUTO_HOOK_SECONDS", "5"))
    except ValueError:
        seconds = 5.0
    try:
        from hooks import add_hook_to_video, HOOK_STYLES
        if style not in HOOK_STYLES:
            style = "classic"
        output_dir = os.path.dirname(clip_path)
        out_path = os.path.join(
            output_dir, f"hooked_{time.time_ns()}_{os.path.basename(clip_path)}")
        add_hook_to_video(clip_path, text, out_path, position="top",
                          duration=seconds, style=style)
        print(f"   🪝 Hook burned ({style}, {seconds:g}s): {text}")
        return out_path, {"text": text, "style": style, "position": "top",
                          "duration_seconds": seconds}
    except Exception as e:
        print(f"   ⚠️ Auto-hook failed ({type(e).__name__}: {e}) — "
              f"delivering the clip without it.")
        return None


def render_clip(input_video, final_output_video, output_format="auto",
                force_strategy=None, crop_overrides=None):
    """Route a cut clip through the right renderer for the chosen output format.
    vertical/auto -> 9:16 reframe, square -> 1:1 reframe, horizontal -> keep.
    ``force_strategy`` (e.g. 'WIDE'/'TRACK') pins every scene's layout — the
    clip editor's whole-clip framing override. ``crop_overrides`` positions
    individual scenes by hand (the per-scene reframing editor) and wins over
    ``force_strategy`` for the scenes it names."""
    if output_format == "horizontal":
        return finalize_clip_passthrough(input_video, final_output_video)
    aspect = 1.0 if output_format == "square" else ASPECT_RATIO
    return process_video_to_vertical(input_video, final_output_video, aspect_ratio=aspect,
                                     force_strategy=force_strategy,
                                     crop_overrides=crop_overrides)


# Watermark geometry, as fractions of the clip width/height.
#
# Vertical placement is the whole point: the top and bottom strips of a 9:16
# clip are either black bars or blurred filler (GENERAL layout), so a mark up
# there is cropped away without touching a single pixel of real footage. At 40%
# of the height it sits inside the content band — a 16:9 source letterboxed
# into 9:16 spans roughly 34%-66% — so removing the mark means cutting into the
# picture. Left-aligned, like OpusClip's.
WATERMARK_WIDTH_RATIO = 0.30
WATERMARK_MARGIN_RATIO = 0.05
WATERMARK_Y_RATIO = 0.40
WATERMARK_OPACITY = 0.85


def apply_watermark(video_path):
    """Burn the OpenShorts watermark into a finished clip (free plan).

    One re-encode pass on the final file so every output format (TRACK,
    GENERAL, horizontal passthrough) gets the mark, and later subtitle/hook
    re-encodes keep it — they re-encode the already-marked pixels.
    """
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "assets", "watermark.png")
    if not os.path.exists(logo_path):
        print(f"   ⚠️ Watermark asset missing ({logo_path}); clip kept unmarked.")
        return False

    # Scale the lockup from the clip's real width: overlay can't read the other
    # input's size, and computing it here avoids the deprecated scale2ref.
    try:
        probe = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video_path],
            stderr=subprocess.STDOUT, timeout=60,
        ).decode().strip().split("x")
        vw, vh = int(probe[0]), int(probe[1])
    except Exception as e:
        print(f"   ⚠️ Could not probe clip for watermark ({e}); clip kept unmarked.")
        return False

    wm_w = max(80, int(vw * WATERMARK_WIDTH_RATIO))
    x = int(vw * WATERMARK_MARGIN_RATIO)
    y = int(vh * WATERMARK_Y_RATIO)
    filt = (
        f"[1:v]scale={wm_w}:-1,format=rgba,"
        f"colorchannelmixer=aa={WATERMARK_OPACITY}[wm];"
        f"[0:v][wm]overlay=x={x}:y={y}"
    )
    tmp_path = video_path + ".wm.mp4"
    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", logo_path,
           "-filter_complex", filt,
           *video_encode_args(QUALITY), "-c:a", "copy", *METADATA_SCRUB,
           "-movflags", "+faststart", tmp_path]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=1800)
    if result.returncode == 0 and os.path.exists(tmp_path):
        os.replace(tmp_path, video_path)
        return True
    err = (result.stderr or b"").decode(errors="ignore")[-300:]
    print(f"   ⚠️ Watermark pass failed (clip kept unmarked): {err}")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    return False


def process_video_to_vertical(input_video, final_output_video, aspect_ratio=ASPECT_RATIO,
                              force_strategy=None, crop_overrides=None):
    """
    Core logic to reframe a horizontal video to a target aspect ratio using
    scene detection and Active Speaker Tracking (MediaPipe).
    aspect_ratio: width/height of the output (9/16 vertical, 1.0 square).
    force_strategy / crop_overrides pin layouts and scene crops by hand (v2
    engine only — the v1 loop below has no layout concept beyond its own
    classifier).
    """
    # v2 engine: analyze downscaled, render natively in ffmpeg. Any failure
    # falls back to the v1 frame loop below so a v2 edge case can't kill jobs.
    if os.environ.get("REFRAME_ENGINE", "v2").strip().lower() != "v1":
        try:
            import reframe_v2
            t0 = time.time()
            result = reframe_v2.render(input_video, final_output_video, aspect_ratio,
                                       force_strategy=force_strategy,
                                       crop_overrides=crop_overrides)
            print(f"   ⏱️ Reframe v2 total: {time.time() - t0:.1f}s")
            return result
        except Exception as e:
            # Only v2 honours hand-framed scenes and forced layouts. Falling
            # through to v1 would quietly return an automatically framed clip,
            # and the user would see their correction vanish with no reason
            # given — so surface the failure instead of discarding their input.
            if crop_overrides or force_strategy:
                raise RuntimeError(
                    f"manual framing needs the v2 reframe engine, which failed "
                    f"({type(e).__name__}: {e})") from e
            print(f"   ⚠️ Reframe v2 failed ({type(e).__name__}: {e}) — "
                  f"falling back to v1 frame loop")

    # The v1 loop stages its work next to the final file: a silent video track
    # first, then the source audio, muxed together at the end.
    stem = os.path.splitext(final_output_video)[0]
    silent_video_path = stem + ".v1video.mp4"
    audio_track_path = stem + ".v1audio.aac"
    for stale in (silent_video_path, audio_track_path, final_output_video):
        if os.path.exists(stale):
            os.remove(stale)

    print(f"🎬 Processing clip: {input_video}")
    print("   Step 1: Detecting scenes...")
    scenes, fps = detect_scenes(input_video)
    
    if not scenes:
        # Scene detection found nothing: treat the whole video as one scene.
        print("   ❌ No scenes were detected. Using full video as one scene.")
        probe = cv2.VideoCapture(input_video)
        span = int(probe.get(cv2.CAP_PROP_FRAME_COUNT))
        probe.release()
        from scenedetect import FrameTimecode
        scenes = [(FrameTimecode(0, fps), FrameTimecode(span, fps))]

    print(f"   ✅ Found {len(scenes)} scenes.")

    print("\n   🧠 Step 2: Preparing Active Tracking...")
    original_width, original_height = get_video_resolution(input_video)
    
    # Same delivery floor as the v2 engine — a fallback render is still the clip
    # the user posts, so it must not ship sub-HD. The frame loop below already
    # resizes every cropped frame to these dims, so nothing else changes.
    from reframe_v2 import delivery_size
    OUTPUT_WIDTH, OUTPUT_HEIGHT = delivery_size(original_width, original_height,
                                                aspect_ratio)

    # Initialize Cameraman
    cameraman = SmoothedCameraman(OUTPUT_WIDTH, OUTPUT_HEIGHT, original_width, original_height, aspect_ratio=aspect_ratio)
    
    # --- New Strategy: Per-Scene Analysis ---
    print("\n   🤖 Step 3: Analyzing Scenes for Strategy (Single vs Group)...")
    scene_strategies = analyze_scenes_strategy(input_video, scenes)
    # scene_strategies is a list of 'TRACK' or 'General' corresponding to scenes
    
    print("\n   ✂️ Step 4: Processing video frames...")
    
    # Raw BGR frames stream down a pipe into ffmpeg, which encodes the silent
    # video track; the audio is muxed back in afterwards.
    encoder = subprocess.Popen(
        ['ffmpeg', '-y',
         '-f', 'rawvideo', '-pix_fmt', 'bgr24',
         '-video_size', f'{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}',
         '-framerate', str(fps), '-i', 'pipe:0',
         *video_encode_args(QUALITY_FAST), '-an', silent_video_path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    reader = cv2.VideoCapture(input_video)
    frame_total = int(reader.get(cv2.CAP_PROP_FRAME_COUNT))
    
    frame_number = 0
    current_scene_index = 0
    
    # Pre-calculate scene boundaries
    scene_boundaries = []
    for s_start, s_end in scenes:
        scene_boundaries.append((s_start.get_frames(), s_end.get_frames()))

    # Global tracker for single-person shots
    speaker_tracker = SpeakerTracker(cooldown_frames=30)

    # Per-stage wall time (server-side diagnostics; hidden from cloud logs).
    stage_seconds = {'detect': 0.0, 'write': 0.0}
    loop_started = time.time()

    with tqdm(total=frame_total, desc="   Processing", file=sys.stdout) as pbar:
        while reader.isOpened():
            ret, frame = reader.read()
            if not ret:
                break

            # Update Scene Index
            if current_scene_index < len(scene_boundaries):
                start_f, end_f = scene_boundaries[current_scene_index]
                if frame_number >= end_f and current_scene_index < len(scene_boundaries) - 1:
                    current_scene_index += 1
            
            # Determine Strategy for current frame based on scene
            current_strategy = scene_strategies[current_scene_index] if current_scene_index < len(scene_strategies) else 'TRACK'
            
            # Apply Strategy
            if current_strategy == 'GENERAL':
                # "Plano General" -> Blur Background + Fit Width
                output_frame = create_general_frame(frame, OUTPUT_WIDTH, OUTPUT_HEIGHT)
                
                # Reset cameraman/tracker so they don't drift while inactive
                cameraman.current_center_x = original_width / 2
                cameraman.target_center_x = original_width / 2
                
            else:
                # "Single Speaker" -> Track & Crop

                # Detect every Nth frame for performance (cameraman smooths in
                # between); the much heavier YOLO fallback gets its own stride.
                if frame_number % DETECT_STRIDE == 0:
                    t_det = time.time()
                    candidates = detect_face_candidates(frame)
                    target_box = speaker_tracker.get_target(candidates, frame_number, original_width)
                    if target_box:
                        cameraman.update_target(target_box)
                    elif frame_number % YOLO_FALLBACK_STRIDE == 0:
                        person_box = detect_person_yolo(frame)
                        if person_box:
                            cameraman.update_target(person_box)
                    stage_seconds['detect'] += time.time() - t_det

                # Snap camera on scene change to avoid panning from previous scene position
                is_scene_start = (frame_number == scene_boundaries[current_scene_index][0])

                x1, y1, x2, y2 = cameraman.get_crop_box(force_snap=is_scene_start)

                # Crop
                if y2 > y1 and x2 > x1:
                    cropped = frame[y1:y2, x1:x2]
                    output_frame = cv2.resize(cropped, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LANCZOS4)
                else:
                    output_frame = cv2.resize(frame, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LANCZOS4)

            t_wr = time.time()
            encoder.stdin.write(output_frame.tobytes())
            stage_seconds['write'] += time.time() - t_wr
            frame_number += 1
            pbar.update(1)
    
    loop_total = time.time() - loop_started
    other = loop_total - stage_seconds['detect'] - stage_seconds['write']
    print(f"\n   ⏱️ Frame loop: {loop_total:.1f}s total — "
          f"detect {stage_seconds['detect']:.1f}s, "
          f"encode-wait {stage_seconds['write']:.1f}s, "
          f"decode+render {other:.1f}s ({frame_number} frames)")

    encoder.stdin.close()
    encode_log = encoder.stderr.read().decode()
    encoder.wait()
    reader.release()

    if encoder.returncode != 0:
        print("\n   ❌ FFmpeg frame processing failed.")
        print("   Stderr:", encode_log)
        return False

    print("\n   🔊 Step 5: Extracting audio...")
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', input_video, '-vn', '-c:a', 'copy', audio_track_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        print("\n   ❌ Audio extraction failed (maybe no audio?). Proceeding without audio.")

    print("\n   ✨ Step 6: Merging...")
    mux = ['ffmpeg', '-y', '-i', silent_video_path]
    if os.path.exists(audio_track_path):
        mux += ['-i', audio_track_path]
    mux += ['-c', 'copy', *METADATA_SCRUB, '-movflags', '+faststart', final_output_video]
    try:
        subprocess.run(mux, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"   ✅ Clip saved to {final_output_video}")
    except subprocess.CalledProcessError as e:
        print("\n   ❌ Final merge failed.")
        print("   Stderr:", e.stderr.decode())
        return False

    for leftover in (silent_video_path, audio_track_path):
        if os.path.exists(leftover):
            os.remove(leftover)

    return True

def transcribe_video(video_path):
    print("🎙️  Transcribing video...")
    from transcribe_backends import transcribe_media

    transcript = transcribe_media(video_path)

    print(f"   Detected language '{transcript['language']}', "
          f"{len(transcript['segments'])} segments")
    for segment in transcript['segments']:
        # Print progress to keep user informed (and prevent timeouts feeling)
        print(f"   [{segment['start']:.2f}s -> {segment['end']:.2f}s] {segment['text']}")

    return transcript

def _run_gemini_stage(client, model_name, prompt, schema,
                      label="Gemini analysis"):
    """Compatibility wrapper for existing Gemini retry tests and callers."""
    return clip_ai._run_gemini_stage(
        client, model_name, prompt, schema, label=label, sleep=time.sleep)


def get_viral_clips(transcript_result, video_duration, provider=None):
    """Delegate transcript selection to the provider-agnostic core."""
    return clip_ai.get_viral_clips(
        transcript_result, video_duration,
        provider=provider or os.getenv("AI_PROVIDER") or "gemini")


def get_visual_clips(video_path, video_duration, language="en", provider=None):
    """Clip a SILENT video by vision: Gemini watches the footage and picks the
    most engaging visual moments (no transcript). Returns the same
    {"shorts", "cost_analysis"} shape as get_viral_clips, or None."""
    provider_name = normalize_provider(provider or os.getenv("AI_PROVIDER") or "gemini")
    if provider_name == "openai":
        raise RuntimeError(
            "OpenAI provider currently requires a transcript for clip selection. "
            "Choose Gemini for silent-video visual analysis."
        )
    print("🎥  Silent video — analyzing with Gemini vision (no transcript)...")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY not found.")
        return None
    client = genai.Client(api_key=api_key)
    model_name = os.environ.get("GEMINI_MODEL") or 'gemini-3.1-flash-lite'
    print(f"🎥  Model: {model_name} | uploading {os.path.basename(video_path)}…")

    file_upload = None
    try:
        file_upload = client.files.upload(file=video_path)
        deadline = time.time() + 180
        while True:
            info = client.files.get(name=file_upload.name)
            state = str(getattr(getattr(info, "state", info), "name", "")).upper()
            if state == "ACTIVE":
                break
            if state == "FAILED":
                print("❌ Gemini could not process the video.")
                return None
            if time.time() > deadline:
                print("❌ Gemini video processing timed out.")
                return None
            time.sleep(2)

        # The vision path has no scoring windows to derive a count from, so the
        # env targets (user request) apply directly over the classic 3-15.
        def _env_int(name, default):
            try:
                return max(1, int(os.environ.get(name, "")))
            except ValueError:
                return default
        v_min_clips = _env_int("CLIP_TARGET_MIN", 3)
        v_max_clips = max(v_min_clips, _env_int("CLIP_TARGET_MAX", 15))
        v_min_secs, v_max_secs = clip_duration_bounds()
        prompt = gemini_worker.VISUAL_PROMPT_TEMPLATE.format(
            video_duration=video_duration, language=language,
            min_clips=v_min_clips, max_clips=v_max_clips,
            min_secs=v_min_secs, max_secs=v_max_secs)
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=gemini_worker.VisualResponse,
        )
        response_holder = {}

        def _handle_response(response):
            response_holder["response"] = response
            gemini_worker.raise_if_blocked(response)
            parsed_obj = getattr(response, "parsed", None)
            if parsed_obj is not None:
                return parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
            return gemini_worker._parse_json_response_text(
                gemini_worker._get_response_text(response))

        parsed = gemini_rate_limiter.call_with_retry(
            lambda: client.models.generate_content(
                model=model_name, contents=[file_upload, prompt], config=config),
            label="visual clip selection",
            estimated_tokens=gemini_rate_limiter.estimate_tokens(
                prompt, extra_tokens=max(0, int(video_duration * 300))),
            handle_response=_handle_response,
            non_retryable_exceptions=(gemini_worker.GeminiBlockedError,),
            sleep=time.sleep,
        )
        shorts = parsed.get("shorts") or []
        # Clamp to the real duration; drop anything degenerate.
        clean = []
        for s in shorts:
            s["start"] = max(0.0, float(s.get("start", 0)))
            s["end"] = min(float(video_duration), float(s.get("end", 0)))
            if s["end"] - s["start"] >= 1.0:
                clean.append(s)
        if not clean:
            print("⚠️ Vision pass returned no usable clips.")
            return None

        cost = gemini_worker._calculate_cost_analysis(
            response_holder.get("response"), model_name)
        if cost:
            print(f"💰 Vision cost ({model_name}): ${cost.get('total_cost', 0):.6f}")
        result = {"shorts": clean}
        if cost:
            result["cost_analysis"] = cost
        return result
    except gemini_worker.GeminiBlockedError as e:
        print(f"🚫 {e}")
        raise
    except Exception as e:
        print(f"❌ Gemini vision error: {e}")
        return None
    finally:
        if file_upload is not None:
            try:
                client.files.delete(name=file_upload.name)
            except Exception:
                pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AutoCrop-Vertical with Viral Clip Detection.")
    
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('-i', '--input', type=str, help="Path to the input video file.")
    input_group.add_argument('-u', '--url', type=str, help="YouTube URL to download and process.")
    
    parser.add_argument('-o', '--output', type=str, help="Output directory or file (if processing whole video).")
    parser.add_argument('--keep-original', action='store_true', help="Keep the downloaded YouTube video.")
    parser.add_argument('--skip-analysis', action='store_true', help="Skip AI analysis and convert the whole video.")
    parser.add_argument('--format', type=str, default="auto", choices=["auto", "vertical", "horizontal", "square"],
                        help="Output aspect: vertical/auto (9:16), horizontal (keep 16:9), square (1:1).")
    parser.add_argument('--transcript', type=str,
                        help="Path to a precomputed transcript JSON (transcribe_media shape); skips transcription.")

    args = parser.parse_args()
    output_format = args.format

    script_start_time = time.time()
    
    def _ensure_dir(path: str) -> str:
        """Create directory if missing and return the same path."""
        if path:
            os.makedirs(path, exist_ok=True)
        return path
    
    # 1. Get Input Video
    if args.url:
        # For multi-clip runs, treat --output as an OUTPUT DIRECTORY (create it if needed).
        # For whole-video runs (--skip-analysis), --output can be a file path.
        if args.output and not args.skip_analysis:
            output_dir = _ensure_dir(args.output)
        else:
            # If output is a directory, use it; if it's a filename, use its directory; else default "."
            if args.output and os.path.isdir(args.output):
                output_dir = args.output
            elif args.output and not os.path.isdir(args.output):
                output_dir = os.path.dirname(args.output) or "."
            else:
                output_dir = "."
        
        input_video, video_title = download_youtube_video(args.url, output_dir)
    else:
        input_video = args.input
        video_title = os.path.splitext(os.path.basename(input_video))[0]
        
        if args.output and not args.skip_analysis:
            # For multi-clip runs, treat --output as an OUTPUT DIRECTORY (create it if needed).
            output_dir = _ensure_dir(args.output)
        else:
            # If output is a directory, use it; if it's a filename, use its directory; else default to input dir.
            if args.output and os.path.isdir(args.output):
                output_dir = args.output
            elif args.output and not os.path.isdir(args.output):
                output_dir = os.path.dirname(args.output) or os.path.dirname(input_video)
            else:
                output_dir = os.path.dirname(input_video)

    if not os.path.exists(input_video):
        print(f"❌ Input file not found: {input_video}")
        exit(1)

    analysis_provider = normalize_provider(os.getenv("AI_PROVIDER") or "gemini")

    # Layout choice is per SOURCE video, not per clip: one upload and one call
    # instead of one per clip, and the answer is a property of the material
    # ("this is a screencast"), which does not change between its own clips.
    # It runs before any render so the modules are switched on in time.
    if layout_picker.ENABLED:
        try:
            _cap = cv2.VideoCapture(input_video)
            _fps = _cap.get(cv2.CAP_PROP_FPS) or 30.0
            _duration = int(_cap.get(cv2.CAP_PROP_FRAME_COUNT)) / _fps
            _cap.release()
            layout_picker.pick_and_apply(input_video, _duration)
        except Exception as e:
            print(f"⚠️ Layout choice skipped ({e}) — using the default layout.")

    # 2. Decision: Analyze clips or process whole?
    if args.skip_analysis:
        print("⏩ Skipping analysis, processing entire video...")
        output_file = args.output if args.output else os.path.join(output_dir, f"{video_title}_vertical.mp4")
        render_clip(input_video, output_file, output_format)
    else:
        # Get duration (needed by both the transcript and the vision path).
        cap = cv2.VideoCapture(input_video)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps
        cap.release()

        # 3. Transcribe — unless the video has no audio, in which case fall back
        # to Gemini vision (picks clips from the imagery instead of the speech).
        from transcribe_backends import NoAudioError
        transcript = None
        # Module handover (issue #68): another module already transcribed this
        # exact source with the same backend, so reuse its output. Any problem
        # with the file falls back to transcribing normally rather than failing.
        if args.transcript:
            try:
                with open(args.transcript, 'r') as f:
                    transcript = json.load(f)
                if not transcript.get('segments'):
                    raise ValueError("transcript has no segments")
                print(f"⏩ Reusing precomputed transcript "
                      f"({len(transcript['segments'])} segments) — skipping transcription.")
            except Exception as e:
                print(f"⚠️ Could not use precomputed transcript ({e}) — transcribing normally.")
                transcript = None
        if transcript is None:
            try:
                transcript = transcribe_video(input_video)
            except NoAudioError as e:
                print(f"🔇 {e} — switching to visual analysis.")

        # 4. Selected-provider analysis (transcript-driven, or Gemini vision for
        # silent videos; OpenAI explicitly fails rather than falling through).
        if transcript is not None:
            clips_data = get_viral_clips(
                transcript, duration, provider=analysis_provider)
        else:
            clips_data = get_visual_clips(
                input_video, duration, provider=analysis_provider)

        if not clips_data or 'shorts' not in clips_data:
            # Deliberately fail instead of reframing the whole video: that path
            # wrote no metadata.json, so app.py marked the job failed anyway
            # (app.py:1087) after burning GPU on a render nobody could see.
            raise RuntimeError(
                f"Clip detection failed — {analysis_provider} did not return usable clips for this video.")
        else:
            print(f"🔥 Found {len(clips_data['shorts'])} clips!")

            # Save metadata. Silent videos have no transcript → no subtitles,
            # which is correct (there's no speech to caption).
            clips_data['transcript'] = transcript or {"language": "none", "segments": []}
            # The clip editor's re-render path needs to find the source video
            # again and reproduce the render settings, so record both. The
            # basename is enough — the file sits in the job dir (URL jobs with
            # --keep-original) or in uploads/ (upload jobs).
            clips_data['source_video'] = os.path.basename(input_video)
            clips_data['output_format'] = output_format
            metadata_file = os.path.join(output_dir, f"{video_title}_metadata.json")
            with open(metadata_file, 'w') as f:
                json.dump(clips_data, f, indent=2)
            print(f"   Saved metadata to {metadata_file}")

            # 5. Process clips in parallel: each worker cuts + renders one
            # clip. Renders are mostly ffmpeg subprocesses (parallelize well);
            # detector inference is serialized internally via DETECT_LOCK.
            def _process_one_clip(i, clip):
                start = clip['start']
                end = clip['end']
                print(f"\n🎬 Processing Clip {i+1}: {start}s - {end}s")
                print(f"   Title: {clip.get('video_title_for_youtube_short', 'No Title')}")

                clip_filename = f"{video_title}_clip_{i+1}.mp4"
                clip_temp_path = os.path.join(output_dir, f"temp_{clip_filename}")
                clip_final_path = os.path.join(output_dir, clip_filename)

                try:
                    # ffmpeg cut — re-encoding for precision on strict seconds
                    cut_command = [
                        'ffmpeg', '-y',
                        '-ss', str(start),
                        '-to', str(end),
                        '-i', input_video,
                        *video_encode_args(QUALITY_FAST),
                        *audio_encode_args(),
                        clip_temp_path
                    ]
                    subprocess.run(cut_command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

                    success = render_clip(clip_temp_path, clip_final_path, output_format)
                    # Layer order: watermark burns into the canonical (so any
                    # later hook replacement, which re-derives from it, keeps
                    # the branding), the hook is a derived hooked_ file, and
                    # captions go last on top of whichever is current. Each
                    # worker writes only its own clip dict, so the re-dump
                    # after the pool is race-free.
                    if success and os.environ.get("WATERMARK") == "1":
                        apply_watermark(clip_final_path)
                    deliver_path = clip_final_path
                    if success and os.environ.get("AUTO_HOOK") == "1":
                        hooked = auto_hook_clip(clip_final_path, clip)
                        if hooked:
                            deliver_path, clip['auto_hook'] = hooked
                    if success:
                        captioned = auto_caption_clip(
                            deliver_path, transcript, start, end)
                        if captioned:
                            deliver_path = captioned["path"]
                            clip.update({
                                "video_url": f"/videos/{os.path.basename(os.path.normpath(output_dir))}/{os.path.basename(deliver_path)}",
                                "server_file": os.path.basename(deliver_path),
                                "render_revision": captioned["revision"],
                                "subtitle_config": captioned["subtitle_config"],
                            })
                        else:
                            clip.update({
                                "video_url": f"/videos/{os.path.basename(os.path.normpath(output_dir))}/{os.path.basename(deliver_path)}",
                                "server_file": os.path.basename(deliver_path),
                                "subtitle_config": None,
                            })
                        print(f"   ✅ Clip {i+1} ready: {os.path.basename(deliver_path)}")
                    return success
                finally:
                    if os.path.exists(clip_temp_path):
                        os.remove(clip_temp_path)

            clip_workers = max(int(os.environ.get("CLIP_WORKERS", "3")), 1)
            shorts = clips_data['shorts']
            with ThreadPoolExecutor(max_workers=min(clip_workers, len(shorts))) as pool:
                futures = {pool.submit(_process_one_clip, i, clip): i
                           for i, clip in enumerate(shorts)}
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"   ❌ Clip {i+1} failed: {type(e).__name__}: {e}")

            # Persist every worker's exact current file and subtitle recipe.
            # The completion handler must not infer the current artifact from
            # mtime: a late FFmpeg write or an older restyle is not ownership.
            with open(metadata_file, 'w') as f:
                json.dump(clips_data, f, indent=2)

    # Clean up original if requested
    if args.url and not args.keep_original and os.path.exists(input_video):
        os.remove(input_video)
        print(f"🗑️  Cleaned up downloaded video.")

    total_time = time.time() - script_start_time
    print(f"\n⏱️  Total execution time: {total_time:.2f}s")
