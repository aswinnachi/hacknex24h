import os
import sys
import time
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def test_queries():
    print("==================================================")
    print("TESTING 7 GROUNDED QUERIES (SECTION 9)")
    print("==================================================")
    
    # 1. Fetch current active camera
    cams_res = requests.get(f"{BASE_URL}/api/cameras")
    assert cams_res.status_code == 200
    cams = cams_res.json()
    assert len(cams) > 0, "No cameras found in DB"
    cam = cams[0]
    cam_id = cam["id"]
    duration = cam["duration"]
    print(f"Target Camera: {cam_id} ({cam['name']}) | Duration: {duration:.2f}s | Dataset: {cam.get('dataset_id')}")

    test_cases = [
        {"query": "person", "expected_verdict": "MATCH", "verify_attr": lambda a: a.get("object_type") == "person"},
        {"query": "person with bag", "expected_verdict": "MATCH", "verify_attr": lambda a: a.get("has_bag") is True},
        {"query": "person without bag", "expected_verdict": "MATCH", "verify_attr": lambda a: a.get("has_bag") is False},
        {"query": "white car", "expected_verdict": "NO MATCH", "verify_attr": None},
        {"query": "red car", "expected_verdict": "NO MATCH", "verify_attr": None},
        {"query": "moving white car", "expected_verdict": "NO MATCH", "verify_attr": None},
        {"query": "moving red car", "expected_verdict": "NO MATCH", "verify_attr": None},
    ]

    for tc in test_cases:
        q = tc["query"]
        print(f"\n--- Testing Query: '{q}' ---")
        res = requests.post(f"{BASE_URL}/api/query", json={"query": q, "camera_id": cam_id})
        assert res.status_code == 200
        data = res.json()
        verdict = data.get("verdict")
        summary = data.get("summary")
        results = data.get("results", [])
        
        print(f"Verdict: {verdict}")
        print(f"Summary: {summary}")
        
        assert verdict == tc["expected_verdict"], f"Expected {tc['expected_verdict']}, got {verdict}"
        
        if results:
            top = results[0]
            ts = top.get("timestamp")
            tracking_id = top.get("tracking_id")
            attrs = top.get("attributes", {})
            print(f"  Top Match: Track #{tracking_id} at {ts:.2f}s | attrs={attrs}")
            
            # HARD BOUNDARY VALIDATION: Timestamp must be within [0, duration]
            assert 0.0 <= ts <= (duration + 0.5), f"Timestamp {ts:.2f}s exceeds video duration {duration:.2f}s!"
            
            # Verify required attribute condition
            if tc["verify_attr"]:
                assert tc["verify_attr"](attrs), f"Attribute check failed for query '{q}' with attrs {attrs}"
                
        print(f"✓ PASSED: '{q}'")

    print("\n==================================================")
    print("ALL 7 SECTION-9 TEST QUERIES PASSED PERFECTLY!")
    print("==================================================")

if __name__ == "__main__":
    test_queries()
