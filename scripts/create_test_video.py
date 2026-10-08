#!/usr/bin/env python3
"""
scripts/create_test_video.py
Generates a 15-second realistic 768x432 CCTV video containing:
- A moving white car
- A moving person in dark/blue shirt
- No red car, no motorcycle, no bag
"""

import cv2
import numpy as np
import os

def generate_cctv_video(output_path="test_cctv.mp4", duration_sec=15, fps=12):
    width, height = 768, 432
    total_frames = int(duration_sec * fps)
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    
    for i in range(total_frames):
        t = i / float(fps)
        # Background: asphalt street and sidewalk
        frame = np.full((height, width, 3), (70, 70, 75), dtype=np.uint8) # Dark asphalt
        
        # Sidewalk / building curb
        cv2.rectangle(frame, (0, 0), (width, 140), (120, 120, 125), -1)
        # Street lane marking
        for lx in range(0, width, 60):
            cv2.rectangle(frame, (lx, 270), (lx + 30, 276), (220, 220, 220), -1)
            
        # 1. Moving White Car: visible from t=1.0s to t=10.0s, moving left to right
        if 1.0 <= t <= 10.0:
            progress = (t - 1.0) / 9.0
            car_x = int(-140 + progress * (width + 200))
            car_y = 290
            car_w, car_h = 130, 60
            
            # Car Body (Bright White / Silver: BGR 240, 240, 245)
            cv2.rectangle(frame, (car_x, car_y), (car_x + car_w, car_y + car_h), (240, 240, 245), -1)
            # Car Roof / Cabin
            cv2.rectangle(frame, (car_x + 25, car_y - 25), (car_x + car_w - 20, car_y), (235, 235, 240), -1)
            # Windows (tinted dark)
            cv2.rectangle(frame, (car_x + 30, car_y - 20), (car_x + 60, car_y - 2), (50, 60, 70), -1)
            cv2.rectangle(frame, (car_x + 65, car_y - 20), (car_x + car_w - 25, car_y - 2), (50, 60, 70), -1)
            # Wheels (Black)
            cv2.circle(frame, (car_x + 25, car_y + car_h), 14, (20, 20, 20), -1)
            cv2.circle(frame, (car_x + car_w - 25, car_y + car_h), 14, (20, 20, 20), -1)
            # Headlights / Taillights
            cv2.circle(frame, (car_x + car_w - 2, car_y + 15), 5, (200, 255, 255), -1) # Front headlight
            cv2.circle(frame, (car_x + 2, car_y + 15), 5, (40, 40, 230), -1) # Rear red tail light
            
        # 2. Moving Person in dark shirt: visible from t=3.0s to t=13.0s, walking along sidewalk
        if 3.0 <= t <= 13.0:
            p_prog = (t - 3.0) / 10.0
            px = int(width + 40 - p_prog * (width + 80)) # Walking right to left
            py = 90
            
            # Head
            cv2.circle(frame, (px, py), 10, (160, 190, 210), -1)
            # Dark Torso / Shirt (Dark navy blue / black: BGR 40, 30, 25)
            cv2.rectangle(frame, (px - 10, py + 10), (px + 10, py + 45), (45, 35, 30), -1)
            # Pants / Legs
            leg_offset = int(np.sin(t * 8.0) * 6)
            cv2.line(frame, (px - 5, py + 45), (px - 8 + leg_offset, py + 80), (30, 30, 35), 4)
            cv2.line(frame, (px + 5, py + 45), (px + 8 - leg_offset, py + 80), (30, 30, 35), 4)
            
        # CCTV timestamp watermark
        time_str = f"2026-10-08 18:00:{int(t):02d}.{int((t % 1) * 100):02d}"
        cv2.putText(frame, time_str, (20, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
        
        out.write(frame)
        
    out.release()
    print(f"Generated {output_path} ({duration_sec}s, {fps} FPS, {total_frames} frames).")

if __name__ == "__main__":
    generate_cctv_video("test_cctv.mp4")
