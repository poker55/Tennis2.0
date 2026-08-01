#!/usr/bin/env python3
"""
Compact OpenCV prototype for single fixed-camera tennis bounce testing.

This intentionally stays classical: no ML models, no cloud calls, no uploads.
Thresholds are experimental/tunable and meant to be adjusted against real clips.
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import deque

cv2 = None
np = None


STATE_THRESHOLD = 0.55
KALMAN_THRESHOLD = 0.45
MATCH_WINDOW = 4
HISTORY_LEN = 24
MIN_BOUNCE_GAP_FRAMES = 14
BOUNDARY_MARGIN = 0.035


def ensure_deps():
    global cv2, np
    if cv2 is not None and np is not None:
        return
    try:
        import cv2 as cv2_module
        import numpy as np_module
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency. Install with: python3 -m pip install opencv-python numpy") from exc
    cv2 = cv2_module
    np = np_module


def clamp01(v):
    return float(max(0.0, min(1.0, v)))


def order_quad_points(points):
    pts = np.asarray(points, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


def read_background_frame(video_path, max_frames=30):
    cap = cv2.VideoCapture(video_path)
    frames = []
    ok, first = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError(f"Could not read video: {video_path}")
    frames.append(first.astype(np.float32))
    for _ in range(max_frames - 1):
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame.astype(np.float32))
    cap.release()
    return np.mean(frames, axis=0).astype(np.uint8) if len(frames) > 1 else first


def detect_court_line_candidates(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 60, 160)
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180, 65, minLineLength=70, maxLineGap=18)
    candidates = []
    if raw is None:
        return candidates

    h, w = gray.shape[:2]
    for x1, y1, x2, y2 in raw[:, 0]:
        length = math.hypot(x2 - x1, y2 - y1)
        if length < min(w, h) * 0.08:
            continue
        angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180
        near_horizontal = angle < 18 or angle > 162
        near_vertical = 65 < angle < 115
        plausible_diagonal = 18 <= angle <= 65 or 115 <= angle <= 162
        if not (near_horizontal or near_vertical or plausible_diagonal):
            continue
        mask = np.zeros_like(gray)
        cv2.line(mask, (x1, y1), (x2, y2), 255, 3)
        brightness = float(cv2.mean(gray, mask=mask)[0])
        if brightness < 95:
            continue
        margin_ok = 5 <= min(x1, x2) and max(x1, x2) <= w - 5 and 5 <= min(y1, y2) and max(y1, y2) <= h - 5
        candidates.append(
            {
                "line": [int(x1), int(y1), int(x2), int(y2)],
                "length": float(length),
                "angle": float(angle),
                "brightness": brightness,
                "location_ok": bool(margin_ok),
            }
        )

    # Prefer inner singles lines when there are multiple parallel court-line hints:
    # for prototype purposes this means not promoting only the outermost image lines.
    candidates.sort(key=lambda c: (c["location_ok"], c["brightness"], c["length"]), reverse=True)
    return candidates[:40]


def draw_calibration_preview(frame, lines, points=None):
    preview = frame.copy()
    for i, item in enumerate(lines):
        x1, y1, x2, y2 = item["line"]
        color = (0, 210, 255) if i < 16 else (90, 130, 255)
        cv2.line(preview, (x1, y1), (x2, y2), color, 2)
    if points:
        pts = np.asarray(points, dtype=np.int32)
        for i, p in enumerate(pts):
            cv2.circle(preview, tuple(p), 6, (0, 255, 0), -1)
            cv2.putText(preview, str(i + 1), tuple(p + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if len(pts) >= 2:
            cv2.polylines(preview, [pts], len(pts) == 4, (0, 255, 0), 2)
    cv2.putText(
        preview,
        "Select 4 singles-side corners: inner singles boundary is authoritative",
        (20, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 255, 255),
        2,
    )
    cv2.putText(preview, "Click corners, Enter/Space to save, C clear, Q quit", (20, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return preview


def manual_calibration(video_path, calibration_path):
    frame = read_background_frame(video_path)
    lines = detect_court_line_candidates(frame)
    points = []
    window = "Calibration - select 4 singles-side corners"

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < 4:
            points.append([x, y])

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    while True:
        cv2.imshow(window, draw_calibration_preview(frame, lines, points))
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            cv2.destroyWindow(window)
            raise RuntimeError("Calibration cancelled")
        if key == ord("c"):
            points.clear()
        if key in (13, 10, 32) and len(points) == 4:
            break
    cv2.destroyWindow(window)

    image_quad = order_quad_points(points)
    top_quad = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    homography, _ = cv2.findHomography(image_quad, top_quad)
    if homography is None:
        raise RuntimeError("Could not compute homography from selected points")

    calibration = {
        "mode": "single-player-side singles",
        "image_points": image_quad.tolist(),
        "topdown_points": top_quad.tolist(),
        "homography": homography.tolist(),
        "valid_polygon_topdown": top_quad.tolist(),
        "detected_line_candidates": lines,
        "instruction": "select 4 singles-side corners",
    }
    with open(calibration_path, "w", encoding="utf-8") as f:
        json.dump(calibration, f, indent=2)
    print(f"Saved calibration to {calibration_path}")
    return calibration


def load_or_create_calibration(video_path, calibration_path):
    if calibration_path and os.path.exists(calibration_path):
        with open(calibration_path, "r", encoding="utf-8") as f:
            return json.load(f)
    if not calibration_path:
        calibration_path = "calibration.json"
    return manual_calibration(video_path, calibration_path)


def make_kalman(x, y):
    kf = cv2.KalmanFilter(4, 2)
    kf.transitionMatrix = np.array([[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
    kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
    kf.processNoiseCov = np.eye(4, dtype=np.float32) * 0.035
    kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 7.5
    kf.errorCovPost = np.eye(4, dtype=np.float32)
    kf.statePost = np.array([[x], [y], [0], [0]], np.float32)
    return kf


def detect_ball(frame, fgmask, prev):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # Require tennis-ball color inside moving regions: static graphics and dark
    # moving shadows should not become ball candidates.
    color = cv2.inRange(hsv, np.array([22, 45, 70]), np.array([78, 255, 255]))
    motion = cv2.threshold(fgmask, 180, 255, cv2.THRESH_BINARY)[1]
    mask = cv2.bitwise_and(motion, color)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_score = -1.0
    prev_xy = None if prev is None else np.array([prev["x"], prev["y"]], dtype=np.float32)
    h, w = frame.shape[:2]

    for c in contours:
        area = float(cv2.contourArea(c))
        if area < 4 or area > 650:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        max_box = max(22.0, min(w, h) * 0.055)
        if bw < 2 or bh < 2 or bw > max_box or bh > max_box:
            continue
        mean_v = float(cv2.mean(hsv[y : y + bh, x : x + bw, 2])[0])
        if mean_v < 75:
            continue
        perimeter = cv2.arcLength(c, True)
        circularity = 0.0 if perimeter <= 0 else (4.0 * math.pi * area / (perimeter * perimeter))
        if circularity < 0.18:
            continue
        cx, cy = x + bw / 2.0, y + bh / 2.0
        dist_score = 0.35
        if prev_xy is not None:
            dist = float(np.linalg.norm(np.array([cx, cy]) - prev_xy))
            dist_score = 1.0 - clamp01(dist / 140.0)
        size_score = 1.0 - clamp01(abs((bw + bh) / 2.0 - 18.0) / 50.0)
        circ_score = clamp01(circularity)
        color_ratio = float(cv2.countNonZero(color[y : y + bh, x : x + bw])) / float(max(1, bw * bh))
        color_score = clamp01(color_ratio * 2.5)
        score = 0.38 * dist_score + 0.24 * circ_score + 0.16 * size_score + 0.22 * color_score
        if score > best_score:
            best_score = score
            best = {
                "visible": True,
                "x": float(cx),
                "y": float(cy),
                "width": float(bw),
                "height": float(bh),
                "area": area,
                "confidence": clamp01(score),
                "bbox": (int(x), int(y), int(bw), int(bh)),
            }

    if best is None:
        return {"visible": False, "x": "", "y": "", "width": "", "height": "", "area": "", "confidence": 0.0, "bbox": None}
    return best


def enrich_state(obs, history):
    if not obs["visible"]:
        return {**obs, "vx": 0.0, "vy": 0.0, "speed": 0.0, "accel": 0.0, "angle": 0.0, "area_change": 0.0}
    prev = next((h for h in reversed(history) if h["visible"]), None)
    vx = vy = speed = accel = angle = area_change = 0.0
    if prev:
        vx = obs["x"] - prev["x"]
        vy = obs["y"] - prev["y"]
        speed = math.hypot(vx, vy)
        angle = math.degrees(math.atan2(vy, vx)) if speed > 0.001 else 0.0
        area_change = abs(obs["area"] - prev["area"]) / max(1.0, prev["area"])
        prev_speed = prev.get("speed", 0.0)
        accel = abs(speed - prev_speed)
    return {**obs, "vx": vx, "vy": vy, "speed": speed, "accel": accel, "angle": angle, "area_change": area_change}


def state_change_score(history, current):
    if not current["visible"] or len(history) < 2:
        return 0.0
    prev = next((h for h in reversed(history) if h["visible"]), None)
    older = next((h for h in reversed(list(history)[:-1]) if h["visible"]), None)
    if not prev or not older:
        return 0.0

    # Experimental/tunable thresholds. Position and velocity dominate; area alone is weak.
    vy_flip = 1.0 if (prev["vy"] > 1.5 and current["vy"] < -0.5) or (prev["vy"] < -1.5 and current["vy"] > 0.5) else 0.0
    vy_delta = clamp01(abs(current["vy"] - prev["vy"]) / 22.0)
    angle_delta = abs((current["angle"] - prev["angle"] + 180.0) % 360.0 - 180.0)
    direction = clamp01(angle_delta / 95.0)
    speed_drop = clamp01((prev["speed"] - current["speed"]) / max(8.0, prev["speed"]))
    accel = clamp01(current["accel"] / 18.0)
    confidence_drop = clamp01((prev["confidence"] - current["confidence"]) / 0.65)
    area_shape = clamp01(current["area_change"] / 1.1)
    return clamp01(0.24 * vy_flip + 0.22 * vy_delta + 0.18 * direction + 0.16 * speed_drop + 0.14 * accel + 0.04 * confidence_drop + 0.02 * area_shape)


def map_point(homography, x, y):
    if x == "" or y == "":
        return "", ""
    src = np.array([[[float(x), float(y)]]], dtype=np.float32)
    dst = cv2.perspectiveTransform(src, np.asarray(homography, dtype=np.float32))[0, 0]
    return float(dst[0]), float(dst[1])


def classify_topdown(point, polygon, margin=BOUNDARY_MARGIN):
    cx, cy = point
    if cx == "" or cy == "":
        return "unknown", ""
    poly = np.asarray(polygon, dtype=np.float32)
    dist = cv2.pointPolygonTest(poly, (float(cx), float(cy)), True)
    if abs(dist) <= margin:
        return "singles_side", "uncertain"
    return "singles_side", "inside" if dist > 0 else "outside"


def strongest_candidate(recent):
    has_state_peak = any(e["state"] > STATE_THRESHOLD for e in recent)
    has_kalman_peak = any(e["kalman"] > KALMAN_THRESHOLD for e in recent)
    if not (has_state_peak and has_kalman_peak):
        return None
    return max(recent, key=lambda e: e["combined"])


def write_overlay(frame, state, predicted, calibration, event_text):
    out = frame.copy()
    for item in calibration.get("detected_line_candidates", [])[:20]:
        x1, y1, x2, y2 = item["line"]
        cv2.line(out, (x1, y1), (x2, y2), (0, 170, 255), 1)
    pts = np.asarray(calibration["image_points"], dtype=np.int32)
    cv2.polylines(out, [pts], True, (0, 255, 80), 2)

    if predicted is not None:
        cv2.circle(out, (int(predicted[0]), int(predicted[1])), 5, (255, 0, 255), 2)
    if state["visible"]:
        x, y = int(state["x"]), int(state["y"])
        cv2.circle(out, (x, y), max(4, int(max(state["width"], state["height"]) / 2)), (0, 255, 255), 2)
        if state.get("bbox"):
            bx, by, bw, bh = state["bbox"]
            cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (255, 255, 0), 1)

    lines = [
        f"inside: {state.get('inside', '')}",
        f"stateChangeScore: {state.get('state_change_score', 0):.2f}",
        f"kalmanEventScore: {state.get('kalman_event_score', 0):.2f}",
        event_text,
    ]
    for i, text in enumerate([t for t in lines if t]):
        cv2.putText(out, text, (18, 30 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (20, 20, 20), 4)
        cv2.putText(out, text, (18, 30 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    return out


def process_video(args):
    ensure_deps()
    calibration = load_or_create_calibration(args.video, args.calibration)
    homography = calibration["homography"]
    valid_polygon = calibration.get("valid_polygon_topdown", [[0, 0], [1, 0], [1, 1], [0, 1]])

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    bg = cv2.createBackgroundSubtractorMOG2(history=220, varThreshold=24, detectShadows=True)

    history = deque(maxlen=HISTORY_LEN)
    recent_scores = deque(maxlen=MATCH_WINDOW)
    pending = None
    kalman = None
    last_bounce_frame = -10_000
    inside_bounces_since_hit = 0
    last_hitter = None
    summaries = []

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "frame_id",
            "time_ms",
            "visible",
            "x",
            "y",
            "vx",
            "vy",
            "area",
            "width",
            "height",
            "confidence",
            "state_change_score",
            "kalman_event_score",
            "court_x",
            "court_y",
            "court_side",
            "inside",
            "candidate",
            "event",
        ]
        csv_writer = csv.DictWriter(f, fieldnames=fieldnames)
        csv_writer.writeheader()

        frame_id = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_id += 1
            time_ms = int(1000.0 * frame_id / fps)
            fgmask = bg.apply(frame)
            prev_visible = next((h for h in reversed(history) if h["visible"]), None)
            obs = detect_ball(frame, fgmask, prev_visible)
            state = enrich_state(obs, history)

            predicted = None
            residual = 0.0
            kalman_score = 0.0
            if state["visible"]:
                if kalman is None:
                    kalman = make_kalman(state["x"], state["y"])
                pred = kalman.predict()
                predicted = (float(pred[0, 0]), float(pred[1, 0]))
                residual = math.hypot(state["x"] - predicted[0], state["y"] - predicted[1])
                kalman_score = clamp01(residual / 38.0)  # Experimental/tunable residual scale.
                kalman.correct(np.array([[np.float32(state["x"])], [np.float32(state["y"])]], dtype=np.float32))
            elif kalman is not None:
                pred = kalman.predict()
                predicted = (float(pred[0, 0]), float(pred[1, 0]))

            score = state_change_score(history, state)
            court_x, court_y = map_point(homography, state["x"], state["y"]) if state["visible"] else ("", "")
            court_side, inside = classify_topdown((court_x, court_y), valid_polygon)
            state.update(
                {
                    "state_change_score": score,
                    "kalman_event_score": kalman_score,
                    "court_x": court_x,
                    "court_y": court_y,
                    "court_side": court_side,
                    "inside": inside,
                }
            )

            combined = (score + kalman_score) / 2.0
            recent_scores.append({"frame": frame_id, "state": score, "kalman": kalman_score, "combined": combined, "point": (state["x"], state["y"]), "inside": inside})
            candidate = strongest_candidate(recent_scores) if len(recent_scores) >= 2 else None
            event = ""
            if candidate and frame_id - last_bounce_frame >= MIN_BOUNCE_GAP_FRAMES:
                last_bounce_frame = candidate["frame"]
                if candidate["inside"] == "inside":
                    inside_bounces_since_hit += 1
                    event = f"BOUNCE_INSIDE at frame {candidate['frame']}"
                    if inside_bounces_since_hit >= 2:
                        winner = "Player B" if last_hitter == "A" else "Player A"
                        event = f"DOUBLE_BOUNCE at frame {candidate['frame']}, winner {winner}"
                elif candidate["inside"] == "outside":
                    winner = "Player A" if last_hitter == "A" else "Player B"
                    event = f"OUT at frame {candidate['frame']}, winner {winner}"
                else:
                    event = f"BOUNCE_UNCERTAIN at frame {candidate['frame']}"
                summaries.append(event)
                pending = event

            overlay = write_overlay(frame, state, predicted, calibration, pending or event)
            writer.write(overlay)
            cv2.imshow("tennis debug", overlay)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("a"):
                last_hitter = "A"
                inside_bounces_since_hit = 0
                event = "HIT_A"
            elif key == ord("b"):
                last_hitter = "B"
                inside_bounces_since_hit = 0
                event = "HIT_B"
            elif key == ord("r"):
                last_hitter = None
                inside_bounces_since_hit = 0
                event = "RESET_RALLY"
            if event.startswith("HIT") or event == "RESET_RALLY":
                summaries.append(f"{event} at frame {frame_id}")
                pending = event

            csv_writer.writerow(
                {
                    "frame_id": frame_id,
                    "time_ms": time_ms,
                    "visible": int(state["visible"]),
                    "x": state["x"],
                    "y": state["y"],
                    "vx": state["vx"],
                    "vy": state["vy"],
                    "area": state["area"],
                    "width": state["width"],
                    "height": state["height"],
                    "confidence": state["confidence"],
                    "state_change_score": score,
                    "kalman_event_score": kalman_score,
                    "court_x": court_x,
                    "court_y": court_y,
                    "court_side": court_side,
                    "inside": inside,
                    "candidate": int(candidate is not None),
                    "event": event,
                }
            )
            history.append(state)

    cap.release()
    writer.release()
    cv2.destroyAllWindows()

    print("\nEvent summary:")
    if summaries:
        for item in summaries:
            print(f"- {item}")
    else:
        print("- no events detected")
    print(f"\nWrote debug video: {args.out}")
    print(f"Wrote CSV: {args.csv}")


def parse_args():
    parser = argparse.ArgumentParser(description="Classical OpenCV tennis-ball bounce/in-out prototype.")
    parser.add_argument("--video", required=True, help="Input local video path")
    parser.add_argument("--calibration", default="calibration.json", help="Saved/manual calibration JSON")
    parser.add_argument("--out", default="debug.mp4", help="Debug overlay video path")
    parser.add_argument("--csv", default="measurements.csv", help="Per-frame measurements CSV")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        process_video(parse_args())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
